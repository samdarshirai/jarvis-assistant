import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from langchain_core.messages import AIMessage, HumanMessage

from jarvis.google.auth import ReauthRequired
from jarvis.llm import LLMCreds, LLMProvider
from jarvis.proactive.scheduler import ISSUE_TEXT, REAUTH_TEXT, brief_due, build_scheduler, per_user_jobs, run_for_users, start_proactive
from jarvis.proactive.notify import make_notifier
from jarvis.proactive.store import ProactiveStore
from jarvis.users import User
from tests.fakes import FakeNotifier, FakeProactiveStore, FakeProvider, FakeTenants, FakeUsers, MemoryAudit


def settings(**over):
    base = dict(timezone="Europe/Berlin", brief_enabled=True, brief_time="07:30", mail_poll_minutes=5,
                leave_lead_minutes=30, auto_event_cap=5, telegram_owner_chat_id=42, fcm_credentials_path="",
                weather_city="", openrouter_api_key="sk-operator-env-key")
    return SimpleNamespace(**{**base, **over})


JOBS = {"brief": AsyncMock(), "mail": AsyncMock(), "sweep": AsyncMock()}
WED = datetime(2026, 10, 21, tzinfo=timezone.utc)  # a Wednesday; Berlin is UTC+2, New York UTC-4


def user(uid, tz="Europe/Berlin", owner=False, disabled=False):
    return User(uid, f"u{uid}@example.com", None, tz, owner, disabled)


def runtime(u, llm=None, store=None, calendar=None, gmail=None):
    cal = calendar or MagicMock(list_events=MagicMock(return_value=[]), list_for_proactive=MagicMock(return_value=[]))
    mail = gmail or MagicMock(search_emails=MagicMock(return_value=[]))
    return SimpleNamespace(user=u, proactive=store or FakeProactiveStore(), audit=MemoryAudit(), calendar=cal,
                           tasks=MagicMock(list_tasks=MagicMock(return_value=[])), gmail=mail, llm=llm, tz=u.tz)


def harness(*rts, google=lambda uid: True):
    notifiers = {rt.user.id: FakeNotifier() for rt in rts}
    users = FakeUsers([rt.user for rt in rts])
    tenants = FakeTenants({rt.user.id: rt for rt in rts})

    async def tick(name, now, job=None, s=None):
        await run_for_users(name, job or per_user_jobs(s or settings())[name], users, tenants, google,
                            lambda u: notifiers[u.id], now=now)

    return tick, notifiers, users


def test_jobs_and_triggers_are_registered():
    jobs = {j.id: j for j in build_scheduler(settings(), JOBS).get_jobs()}
    assert set(jobs) == {"brief", "mail", "sweep"}
    assert jobs["brief"].trigger.interval == timedelta(minutes=5)  # a per-user check, not a fixed cron
    assert jobs["mail"].trigger.interval == timedelta(minutes=5)
    assert jobs["sweep"].trigger.interval == timedelta(minutes=5)
    assert all(j.coalesce and j.max_instances == 1 for j in jobs.values())


def test_brief_can_be_disabled_and_intervals_follow_settings():
    jobs = {j.id: j for j in build_scheduler(settings(brief_enabled=False, mail_poll_minutes=10), JOBS).get_jobs()}
    assert set(jobs) == {"mail", "sweep"} and jobs["mail"].trigger.interval == timedelta(minutes=10)


def test_brief_due_window_is_weekdays_from_brief_time_for_an_hour():
    berlin = lambda h, m, day=21: datetime(2026, 10, day, h, m, tzinfo=timezone(timedelta(hours=2)))  # noqa: E731
    assert not brief_due(berlin(7, 29), "07:30")
    assert brief_due(berlin(7, 30), "07:30") and brief_due(berlin(8, 30), "07:30")
    assert not brief_due(berlin(8, 31), "07:30")
    assert not brief_due(berlin(7, 45, day=24), "07:30")  # Saturday


async def test_each_user_gets_own_brief_in_own_timezone(monkeypatch):
    weather_calls = []
    monkeypatch.setattr("jarvis.proactive.brief.weather.fetch",
                        lambda city, tz, now, coords=None: weather_calls.append((city, tz, coords)) or None)
    berlin = runtime(user(1, owner=True), llm=FakeProvider({"fast": [AIMessage("Good morning. Berlin.")]}))
    ny = runtime(user(2, tz="America/New_York"), llm=FakeProvider({"fast": [AIMessage("Good morning. New York.")]}))
    ny.proactive.set_state("brief_time", "08:00")
    ny.proactive.set_state("weather_city", "Boston")
    ny.proactive.set_state("last_location", "42.3600,-71.0600")
    tick, n, _ = harness(berlin, ny)

    await tick("brief", WED.replace(hour=5, minute=40))  # Berlin 07:40, New York 01:40
    assert [c[0] for c in n[1].calls] == ["telegram", "push"] and n[2].calls == []
    await tick("brief", WED.replace(hour=12, minute=10))  # Berlin 14:10, New York 08:10
    assert len(n[1].calls) == 2 and [c[0] for c in n[2].calls] == ["telegram", "push"]
    assert n[2].calls[1][1].startswith("Good morning.")
    assert weather_calls == [("", "Europe/Berlin", None), ("Boston", "America/New_York", (42.36, -71.06))]
    assert berlin.proactive.alerts == {"brief:2026-10-21"} and ny.proactive.alerts == {"brief:2026-10-21"}


async def test_server_weather_city_is_the_owners_only(monkeypatch):
    weather_calls = []
    monkeypatch.setattr("jarvis.proactive.brief.weather.fetch",
                        lambda city, tz, now, coords=None: weather_calls.append(city) or None)
    owner = runtime(user(1, owner=True), llm=FakeProvider({"fast": [AIMessage("Good morning. Owner.")]}))
    friend = runtime(user(2), llm=FakeProvider({"fast": [AIMessage("Good morning. Friend.")]}))
    tick, n, _ = harness(owner, friend)
    await tick("brief", WED.replace(hour=5, minute=40), s=settings(weather_city="Owner City"))
    assert weather_calls == ["Owner City", ""]  # the friend never inherits the operator's city
    assert "Owner City" not in str(n[2].calls) and n[2].calls


async def test_brief_once_per_day_and_survives_restart_within_window():
    store = FakeProactiveStore()  # stands in for the user's Postgres rows, which outlive a restart
    rt = runtime(user(1), store=store, llm=FakeProvider({"fast": [AIMessage("Good morning.")]}))
    tick, n, _ = harness(rt)
    await tick("brief", WED.replace(hour=5, minute=35))
    await tick("brief", WED.replace(hour=5, minute=50))
    assert len(n[1].calls) == 2  # one brief: telegram + push

    tick2, n2, _ = harness(runtime(user(1), store=store, llm=FakeProvider({"fast": [AIMessage("Good morning.")]})))  # restart, same day, still in the window
    await tick2("brief", WED.replace(hour=6, minute=0))
    assert n2[1].calls == []

    late = runtime(user(1), store=FakeProactiveStore(), llm=FakeProvider({"fast": [AIMessage("Good morning.")]}))  # restarted before the brief went out: still sent in the window
    tick3, n3, _ = harness(late)
    await tick3("brief", WED.replace(hour=6, minute=25))
    await tick3("brief", WED.replace(hour=6, minute=35))  # past the hour: never a late duplicate
    assert len(n3[1].calls) == 2


async def test_brief_claim_survives_recreated_postgres_store(pool, uids):
    uid, _ = uids
    first = runtime(user(uid), llm=FakeProvider({"fast": [AIMessage("Good morning.")]}),
                    store=ProactiveStore(pool, uid))
    tick, n, _ = harness(first)
    await tick("brief", WED.replace(hour=5, minute=35))
    assert len(n[uid].calls) == 2
    restarted = runtime(user(uid), llm=FakeProvider({"fast": [AIMessage("Good morning.")]}),
                        store=ProactiveStore(pool, uid))
    tick2, n2, _ = harness(restarted)
    await tick2("brief", WED.replace(hour=6, minute=0))
    assert n2[uid].calls == []


@pytest.mark.parametrize("cursor", [None, "1700000000"])
async def test_user_without_llm_key_skipped_no_operator_fallback(cursor):
    store = FakeProactiveStore()
    if cursor:
        store.set_state("mail_cursor", cursor)
    store.get_state = MagicMock(wraps=store.get_state)
    store.claim_alert = MagicMock(wraps=store.claim_alert)
    rt = runtime(user(2), llm=None, store=store)
    tick, n, _ = harness(rt)
    s = settings()  # the operator's env key is set and must never be used for this user
    await tick("mail", WED.replace(hour=5, minute=40), s=s)
    await tick("brief", WED.replace(hour=5, minute=40), s=s)
    rt.gmail.search_emails.assert_not_called()
    rt.calendar.list_events.assert_not_called()
    rt.tasks.list_tasks.assert_not_called()
    store.get_state.assert_not_called()
    store.claim_alert.assert_not_called()
    assert store.state == ({"mail_cursor": cursor} if cursor else {})
    assert n[2].calls == [] and rt.audit.records == []

    # Adding a key inside today's window can still claim and send today's brief.
    rt.llm = FakeProvider({"fast": [AIMessage("Good morning.")]})
    await tick("brief", WED.replace(hour=5, minute=45), s=s)
    assert store.alerts == {"brief:2026-10-21"} and len(n[2].calls) == 2


async def test_one_user_failing_does_not_stop_others(caplog):
    broken = runtime(user(2), calendar=MagicMock(list_for_proactive=MagicMock(side_effect=RuntimeError("boom"))))
    ok = runtime(user(3))
    no_google = runtime(user(4))
    tick, n, users = harness(broken, ok, no_google, google=lambda uid: uid != 4)
    users.users.insert(0, user(1))  # enabled but its runtime cannot be built
    users.users.append(user(5, disabled=True))
    seen = []

    async def job(rt, notifier, llm, tz, now):
        seen.append(rt.user.id)
        rt.calendar.list_for_proactive(now, now)

    with caplog.at_level(logging.ERROR):
        await tick("sweep", WED, job=job)
    assert seen == [2, 3]
    assert broken.audit.records[0]["name"] == "sweep_error"
    assert "UnknownUser" in caplog.text


@pytest.mark.parametrize("failure", ["timezone", "notifier", "google", "runtime"])
async def test_setup_failure_skips_only_that_user_with_safe_log(failure, caplog):
    secret = "sk-setup-sentinel"
    bad = runtime(user(1, tz=secret if failure == "timezone" else "Europe/Berlin"))
    good = runtime(user(2))
    tenants = FakeTenants({2: good} if failure == "runtime" else {1: bad, 2: good})
    seen = []

    def has_google(uid):
        if uid == 1 and failure == "google":
            raise RuntimeError(secret)
        return True

    def notifier_for(u):
        if u.id == 1 and failure == "notifier":
            raise RuntimeError(secret)
        return FakeNotifier()

    async def job(rt, notifier, llm, tz, now):
        seen.append(rt.user.id)

    with caplog.at_level(logging.ERROR):
        await run_for_users("sweep", job, FakeUsers([bad.user, good.user]), tenants, has_google, notifier_for, WED)
    assert seen == [2]
    assert secret not in caplog.text and "Traceback" not in caplog.text
    assert len(caplog.records) == 1 and caplog.records[0].exc_info is None
    expected = {"timezone": "ZoneInfoNotFoundError", "notifier": "RuntimeError", "google": "RuntimeError",
                "runtime": "UnknownUser"}[failure]
    assert expected in caplog.text


async def test_friend_notice_is_push_only(monkeypatch):
    monkeypatch.setattr("jarvis.proactive.notify.fcm_access_token", lambda path: ("proj", "AT"))
    pushed = []
    monkeypatch.setattr("jarvis.proactive.notify.send_push",
                        lambda project, access, tokens, text, client: pushed.append((tokens, text)) or len(tokens))
    bot = SimpleNamespace(send_message=AsyncMock())
    devices = SimpleNamespace(fcm_tokens=lambda uid: [f"phone-of-{uid}"])
    rt = runtime(user(2))
    await run_for_users("sweep", AsyncMock(side_effect=ReauthRequired("x")), FakeUsers([rt.user]), FakeTenants({2: rt}),
                        lambda uid: True, lambda u: make_notifier(bot, 42, "creds.json", devices, u.id, u.is_owner), now=WED)
    bot.send_message.assert_not_called()
    assert pushed == [(["phone-of-2"], REAUTH_TEXT)]


async def test_issue_notice_once_per_day():
    def rejected():
        r = httpx.Response(401, request=httpx.Request("POST", "https://llm.example/v1/chat/completions"))
        return openai.AuthenticationError("bad key sk-secret", response=r, body=None)

    llm = FakeProvider({"fast": [rejected(), rejected(), rejected()]})
    rt = runtime(user(2), llm=llm)
    tick, n, _ = harness(rt)

    async def uses_llm(rt, notifier, llm, tz, now):
        try:  # like mail watch and the brief, the job swallows the LLM failure itself
            await llm.get("fast").ainvoke([HumanMessage("hi")])
        except Exception:
            pass

    await tick("mail", WED, job=uses_llm)
    await tick("mail", WED + timedelta(minutes=5), job=uses_llm)
    assert n[2].calls == [("telegram", ISSUE_TEXT["rejected_key"], None), ("push", ISSUE_TEXT["rejected_key"], None)]
    await tick("mail", WED + timedelta(days=1), job=uses_llm)
    assert len(n[2].calls) == 4 and "sk-secret" not in str(n[2].calls)

    reauth = AsyncMock(side_effect=ReauthRequired("x"))
    await tick("sweep", WED, job=reauth)
    await tick("sweep", WED, job=reauth)
    assert [c[1] for c in n[2].calls].count(REAUTH_TEXT) == 2  # telegram + push, once
    assert rt.proactive.alerts >= {"issue:rejected_key:2026-10-21", "issue:rejected_key:2026-10-22", "issue:google:2026-10-21"}


async def test_out_of_credit_is_its_own_issue():
    r = httpx.Response(402, request=httpx.Request("POST", "https://llm.example/v1/chat/completions"))
    llm = FakeProvider({"fast": [openai.APIStatusError("no credit", response=r, body=None)]})
    tick, n, _ = harness(runtime(user(2), llm=llm))

    async def uses_llm(rt, notifier, llm, tz, now):
        try:
            await llm.get("fast").ainvoke([HumanMessage("hi")])
        except Exception:
            pass

    await tick("mail", WED, job=uses_llm)
    assert n[2].calls[-1] == ("push", ISSUE_TEXT["out_of_credit"], None)


async def test_mail_state_isolated_between_users(pool, uids):
    a, b = uids
    store_a, store_b = ProactiveStore(pool, a), ProactiveStore(pool, b)
    store_a.set_state("mail_cursor", "1700000000")
    mail = [{"id": "m1", "subject": "Your flight booking", "snippet": ""}]
    gmail = lambda: MagicMock(search_emails=MagicMock(return_value=mail),  # noqa: E731
                              read_email=MagicMock(return_value={"from": "x", "subject": "s", "body": "b"}))
    rt_a = runtime(user(a), llm=FakeProvider({"fast": [AIMessage('{"found": false}')]}), store=store_a, gmail=gmail())
    rt_b = runtime(user(b), llm=None, store=store_b, gmail=gmail())
    tick, n, _ = harness(rt_a, rt_b)
    await tick("mail", WED)
    assert store_a.mail_seen("m1") and not store_b.mail_seen("m1")
    assert store_a.get_state("mail_cursor") != "1700000000"
    assert store_b.get_state("mail_cursor") is None  # no key: no cursor initialization or source reads


async def test_start_proactive_builds_and_starts_the_jobs():
    sched = start_proactive(settings(), FakeTenants({}), FakeUsers([]), SimpleNamespace(send_message=AsyncMock()),
                            SimpleNamespace(fcm_tokens=lambda uid: []))
    try:
        assert sched.running and {j.id for j in sched.get_jobs()} == {"brief", "mail", "sweep"}
    finally:
        sched.shutdown(wait=False)


async def test_registered_mail_job_executes_through_apscheduler_executor():
    users = FakeUsers([])
    users.enabled = MagicMock(wraps=users.enabled)
    completed = asyncio.get_running_loop().create_future()
    sched = start_proactive(settings(), FakeTenants({}), users, SimpleNamespace(send_message=AsyncMock()),
                            SimpleNamespace(fcm_tokens=lambda uid: []))

    def on_job(event):
        if event.job_id == "mail" and not completed.done():
            completed.set_result(event)

    sched.add_listener(on_job, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    try:
        sched.modify_job("mail", next_run_time=datetime.now(timezone.utc))
        event = await asyncio.wait_for(completed, timeout=3)
        if inspect.iscoroutine(getattr(event, "retval", None)):
            event.retval.close()  # regression must not leave the old unawaited coroutine behind
        users.enabled.assert_called_once_with()
        assert getattr(event, "exception", None) is None
        assert not inspect.isawaitable(event.retval)
    finally:
        sched.shutdown(wait=False)


@pytest.mark.parametrize("name", ["mail", "brief"])
@pytest.mark.parametrize("status,kind", [(401, "rejected_key"), (402, "out_of_credit")])
async def test_real_provider_key_echo_never_reaches_proactive_logs_or_audit(name, status, kind, caplog):
    secret = "sk-provider-echo-sentinel"
    audit = MemoryAudit()
    provider = LLMProvider(LLMCreds("https://provider.example/v1", secret, ["fast"], ["strong"]), audit)
    await provider.http.aclose()
    provider.http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": {"message": f"key {secret} rejected"}})))
    store = FakeProactiveStore()
    store.set_state("mail_cursor", "1000")
    gmail = MagicMock(search_emails=MagicMock(return_value=[{"id": "m1", "from": "airline", "subject": "Flight booking", "snippet": ""}]),
                      read_email=MagicMock(return_value={"from": "airline", "subject": "Flight booking", "body": "Flight"}))
    rt = runtime(user(2), llm=provider, store=store, gmail=gmail)
    rt.audit = audit
    tick, n, _ = harness(rt)
    try:
        with caplog.at_level(logging.ERROR):
            await tick(name, WED.replace(hour=5, minute=40))
        assert secret not in caplog.text and secret not in str(audit.records)
        assert all(record.exc_info is None for record in caplog.records)
        assert "Traceback" not in caplog.text
        assert n[2].calls[-1] == ("push", ISSUE_TEXT[kind], None)
        assert store.alerts >= {f"issue:{kind}:2026-10-21"}
        if name == "mail":
            error = next(r for r in audit.records if r["name"] == "mail_error")
            assert set(error["result"]) == {"error"}
            assert error["result"]["error"].isidentifier() and error["result"]["error"].endswith("Error")
        else:
            assert any(r["name"] == "brief" for r in audit.records)
    finally:
        await provider.aclose()


@pytest.mark.parametrize("sink", ["job", "audit", "notice"])
async def test_outer_job_and_failure_helpers_log_only_exception_types(sink, caplog):
    secret = "sk-job-echo-sentinel"
    rt = runtime(user(2))
    tick, n, _ = harness(rt)
    if sink == "audit":
        rt.audit.record = MagicMock(side_effect=ValueError(secret))
    if sink == "notice":
        n[2].both = AsyncMock(side_effect=ValueError(secret))
    job = AsyncMock(side_effect=ReauthRequired(secret) if sink == "notice" else RuntimeError(secret))
    with caplog.at_level(logging.ERROR):
        await tick("sweep", WED, job=job)
    assert secret not in caplog.text and secret not in str(rt.audit.records)
    assert caplog.records and all(r.exc_info is None for r in caplog.records)
    assert "Traceback" not in caplog.text
    if sink == "job":
        assert rt.audit.records[0]["result"] == {"error": "RuntimeError"}


@pytest.mark.parametrize("status,kind", [(401, "rejected_key"), (403, "rejected_key"), (402, "out_of_credit")])
async def test_sanitized_provider_error_keeps_issue_classification(status, kind):
    error = RuntimeError("LLM request failed")
    error.status_code = status  # Task 8 preserves status without an unsafe provider response body
    tick, n, _ = harness(runtime(user(2), llm=FakeProvider({"fast": [error]})))

    async def uses_llm(rt, notifier, llm, tz, now):
        await llm.get("fast").ainvoke([HumanMessage("hi")])

    await tick("mail", WED, job=uses_llm)
    assert n[2].calls[-1] == ("push", ISSUE_TEXT[kind], None)
