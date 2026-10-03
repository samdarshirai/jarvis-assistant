from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.google.auth import ReauthRequired
from jarvis.proactive.scheduler import REAUTH_TEXT, build_scheduler, guarded, start_proactive
from tests.fakes import FakeNotifier, FakeProactiveStore, MemoryAudit


def settings(**over):
    base = dict(timezone="Europe/Berlin", brief_enabled=True, brief_time="07:30", mail_poll_minutes=5,
                leave_lead_minutes=30, auto_event_cap=5, telegram_owner_chat_id=42, fcm_credentials_path="")
    return SimpleNamespace(**{**base, **over})


JOBS = {"brief": AsyncMock(), "mail": AsyncMock(), "sweep": AsyncMock()}


def test_jobs_and_triggers_are_registered():
    jobs = {j.id: j for j in build_scheduler(settings(), JOBS).get_jobs()}
    assert set(jobs) == {"brief", "mail", "sweep"}
    cron = str(jobs["brief"].trigger)
    assert "day_of_week='mon-fri'" in cron and "hour='7'" in cron and "minute='30'" in cron
    assert jobs["mail"].trigger.interval == timedelta(minutes=5)
    assert jobs["sweep"].trigger.interval == timedelta(minutes=5)
    assert all(j.coalesce and j.max_instances == 1 for j in jobs.values())
    assert jobs["brief"].misfire_grace_time == 3600


def test_brief_can_be_disabled_and_intervals_follow_settings():
    jobs = {j.id: j for j in build_scheduler(settings(brief_enabled=False, mail_poll_minutes=10, brief_time="06:05"), JOBS).get_jobs()}
    assert set(jobs) == {"mail", "sweep"} and jobs["mail"].trigger.interval == timedelta(minutes=10)
    brief = {j.id: j for j in build_scheduler(settings(brief_time="06:05"), JOBS).get_jobs()}["brief"]
    assert "hour='6'" in str(brief.trigger) and "minute='5'" in str(brief.trigger)


async def test_guarded_runs_the_job():
    job = AsyncMock()
    await guarded("mail", job, FakeNotifier(), FakeProactiveStore(), MemoryAudit(), "Europe/Berlin")()
    job.assert_awaited_once()


async def test_reauth_sends_one_notice_per_day_and_never_raises():
    n, store = FakeNotifier(), FakeProactiveStore()
    run = guarded("brief", AsyncMock(side_effect=ReauthRequired("x")), n, store, MemoryAudit(), "Europe/Berlin")
    await run()
    await run()
    assert n.calls == [("telegram", REAUTH_TEXT, None)]


async def test_other_errors_are_audited_and_swallowed():
    audit = MemoryAudit()
    await guarded("sweep", AsyncMock(side_effect=RuntimeError("boom")), FakeNotifier(), FakeProactiveStore(), audit, "Europe/Berlin")()
    assert audit.records[0]["name"] == "sweep_error" and audit.records[0]["result"] == {"error": "boom"}


async def test_a_failing_notice_does_not_escape():
    class Broken(FakeNotifier):
        async def telegram(self, text, undo_event_id=None):
            raise RuntimeError("telegram down")

    await guarded("brief", AsyncMock(side_effect=ReauthRequired("x")), Broken(), FakeProactiveStore(), MemoryAudit(), "Europe/Berlin")()


async def test_start_proactive_builds_and_starts_the_jobs():
    sched = start_proactive(settings(), FakeProactiveStore(), MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                            MemoryAudit(), SimpleNamespace(send_message=AsyncMock()), SimpleNamespace(fcm_tokens=lambda: []))
    try:
        assert sched.running and {j.id for j in sched.get_jobs()} == {"brief", "mail", "sweep"}
    finally:
        sched.shutdown(wait=False)
