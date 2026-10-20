import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
from langchain_core.messages import AIMessage, HumanMessage

from jarvis.google.auth import ReauthRequired
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
    berlin, ny = runtime(user(1, owner=True)), runtime(user(2, tz="America/New_York"))
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


async def test_brief_once_per_day_and_survives_restart_within_window():
    store = FakeProactiveStore()  # stands in for the user's Postgres rows, which outlive a restart
    rt = runtime(user(1), store=store)
    tick, n, _ = harness(rt)
    await tick("brief", WED.replace(hour=5, minute=35))
    await tick("brief", WED.replace(hour=5, minute=50))
    assert len(n[1].calls) == 2  # one brief: telegram + push

    tick2, n2, _ = harness(runtime(user(1), store=store))  # restart, same day, still in the window
    await tick2("brief", WED.replace(hour=6, minute=0))
    assert n2[1].calls == []

    late = runtime(user(1), store=FakeProactiveStore())  # restarted before the brief went out: still sent in the window
    tick3, n3, _ = harness(late)
    await tick3("brief", WED.replace(hour=6, minute=25))
    await tick3("brief", WED.replace(hour=6, minute=35))  # past the hour: never a late duplicate
    assert len(n3[1].calls) == 2


async def test_user_without_llm_key_skipped_no_operator_fallback():
    store = FakeProactiveStore()
    store.set_state("mail_cursor", "1700000000")
    rt = runtime(user(2), llm=None, store=store)
    tick, n, _ = harness(rt)
    s = settings()  # the operator's env key is set and must never be used for this user
    await tick("mail", WED.replace(hour=5, minute=40), s=s)
    rt.gmail.search_emails.assert_not_called()
    await tick("brief", WED.replace(hour=5, minute=40), s=s)
    assert rt.audit.records[-1]["name"] == "brief" and rt.audit.records[-1]["result"]["llm"] is False
    assert n[2].calls[1][1].startswith("Good morning.")


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
    assert store_b.get_state("mail_cursor") == str(int(WED.timestamp()))  # B's first run only sets its own cursor


async def test_start_proactive_builds_and_starts_the_jobs():
    sched = start_proactive(settings(), FakeTenants({}), FakeUsers([]), SimpleNamespace(send_message=AsyncMock()),
                            SimpleNamespace(fcm_tokens=lambda uid: []))
    try:
        assert sched.running and {j.id for j in sched.get_jobs()} == {"brief", "mail", "sweep"}
    finally:
        sched.shutdown(wait=False)
