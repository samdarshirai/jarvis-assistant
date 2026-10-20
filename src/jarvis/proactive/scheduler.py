import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from langchain_core.callbacks import BaseCallbackHandler

from jarvis import weather
from jarvis.google.auth import PROVIDER, PgTokenStore, ReauthRequired
from jarvis.llm_check import _code as llm_error_code
from jarvis.proactive.alerts import sweep
from jarvis.proactive.brief import run_brief
from jarvis.proactive.mailwatch import MailWatch
from jarvis.proactive.notify import make_notifier

log = logging.getLogger(__name__)
# ponytail: mail watch auto-creates calendar events, and the app has no Undo for them yet (Undo is a Telegram button,
# the owner's only), so it runs for the owner only; set True once the app can undo an auto-added event.
FRIEND_MAIL_WATCH = False
REAUTH_TEXT = "Jarvis needs Google access again. Open the app and sign in."
ISSUE_TEXT = {
    "google": REAUTH_TEXT,
    "rejected_key": "Your LLM key was rejected. Open Settings in the app.",
    "out_of_credit": "Your LLM account is out of credit.",
}


class WatchedLLM(BaseCallbackHandler):
    """The user's LLM provider, remembering a rejected key or exhausted credit: mail watch and the brief swallow
    LLM errors themselves, so the scheduler learns of them through this callback."""
    run_inline = True

    def __init__(self, llm):
        self.llm, self.issue = llm, None

    def get(self, tier: str, tools: list | None = None):
        return self.llm.get(tier, tools).with_config(callbacks=[self])

    def on_llm_error(self, error, **kwargs):
        r = getattr(error, "response", None)  # openai's APIStatusError carries the provider's httpx response
        if not isinstance(r, httpx.Response) and getattr(error, "status_code", None) in (401, 402, 403):
            r = httpx.Response(error.status_code)  # sanitized provider failures retain only the status
        code = llm_error_code(r) if isinstance(r, httpx.Response) else None
        if code in ISSUE_TEXT:
            self.issue = code


def brief_due(now: datetime, brief_time: str) -> bool:
    """Weekday, and `now` (the user's local time) within [brief_time, brief_time + 60 min]."""
    hour, minute = (int(p) for p in brief_time.split(":"))
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now.weekday() < 5 and start <= now <= start + timedelta(minutes=60)


async def _notice(rt, notifier, kind: str, day: str) -> None:
    try:
        if await asyncio.to_thread(rt.proactive.claim_alert, f"issue:{kind}:{day}"):
            await notifier.both(ISSUE_TEXT[kind])
    except Exception as e:
        log.error("could not send the %s notice: %s", kind, type(e).__name__)


async def run_user(name: str, job, rt, notifier, tz: str, now: datetime) -> None:
    """One user's run of one job; never raises. Google reauth and LLM key problems become a once-a-day notice."""
    llm = WatchedLLM(rt.llm) if rt.llm is not None else None  # no key: the jobs skip the LLM, never the operator's key
    issue = None
    try:
        await job(rt, notifier, llm, tz, now)
    except ReauthRequired:
        issue = "google"
    except Exception as e:
        log.error("proactive job %s failed for user %s: %s", name, rt.user.id, type(e).__name__)
        try:
            await asyncio.to_thread(rt.audit.record, "proactive", f"{name}_error", result={"error": type(e).__name__})
        except Exception as audit_error:
            log.error("could not audit the failure: %s", type(audit_error).__name__)
    for kind in filter(None, (issue, llm and llm.issue)):
        await _notice(rt, notifier, kind, now.date().isoformat())


async def run_for_users(name: str, job, users, tenants, has_google, notifier_for, now: datetime | None = None) -> None:
    """Runs `job` for every enabled user (re-read each tick), each isolated: one user's failure never stops the others."""
    now = now or datetime.now(timezone.utc)
    for user in await asyncio.to_thread(users.enabled):
        try:
            if not await asyncio.to_thread(has_google, user.id):
                continue  # never connected Google: nothing to watch
            rt = await tenants.get(user.id)
            await run_user(name, job, rt, notifier_for(user), user.tz, now.astimezone(ZoneInfo(user.tz)))
        except Exception as e:
            log.error("proactive %s skipped user %s: %s", name, user.id, type(e).__name__)  # the type only: no secrets
            continue


def per_user_jobs(s) -> dict:
    async def mail(rt, notifier, llm, tz, now):
        if not (rt.user.is_owner or FRIEND_MAIL_WATCH):
            return
        await MailWatch(rt.gmail, rt.calendar, rt.proactive, notifier, llm, rt.audit, tz, s.auto_event_cap).run_once(now)

    async def alerts(rt, notifier, llm, tz, now):
        await sweep(rt.calendar, rt.proactive, notifier, tz, s.leave_lead_minutes, now)

    async def brief(rt, notifier, llm, tz, now):
        if llm is None:  # don't consume today's claim before the user sets their own key
            return
        state = lambda key: asyncio.to_thread(rt.proactive.get_state, key)  # noqa: E731
        try:
            due = brief_due(now, await state("brief_time") or s.brief_time)
        except ValueError:  # a malformed stored time: the server default
            due = brief_due(now, s.brief_time)
        # ponytail: claim before sending = at most one brief a day; a failed send is not retried
        if not due or not await asyncio.to_thread(rt.proactive.claim_alert, f"brief:{now.date().isoformat()}"):
            return
        await run_brief(rt.calendar, rt.tasks, rt.gmail, llm, notifier, rt.audit, tz, now=now,
                        weather_city=await state("weather_city") or (s.weather_city if rt.user.is_owner else ""),
                        location=lambda: weather.load_coords(rt.proactive))

    return {"brief": brief, "mail": mail, "sweep": alerts}


def build_scheduler(s, jobs: dict) -> AsyncIOScheduler:
    # in-memory jobstore on purpose: jobs are re-registered at startup and all durable state is in Postgres
    sched = AsyncIOScheduler(timezone=s.timezone)
    common = dict(coalesce=True, max_instances=1, misfire_grace_time=60)
    if s.brief_enabled:  # each user's brief time is checked every tick; brief:<date> keeps it to once a day
        sched.add_job(jobs["brief"], IntervalTrigger(minutes=5), id="brief", **common)
    sched.add_job(jobs["mail"], IntervalTrigger(minutes=s.mail_poll_minutes), id="mail", **common)
    sched.add_job(jobs["sweep"], IntervalTrigger(minutes=5), id="sweep", **common)
    return sched


def start_proactive(s, tenants, users, bot, devices) -> AsyncIOScheduler:
    def has_google(user_id: int) -> bool:
        return PgTokenStore(tenants.pool, user_id).get(PROVIDER) is not None

    def notifier_for(user):
        return make_notifier(bot, s.telegram_owner_chat_id, s.fcm_credentials_path, devices, user.id, user.is_owner)

    def each(name, job):
        async def run():
            await run_for_users(name, job, users, tenants, has_google, notifier_for)
        return run

    sched = build_scheduler(s, {name: each(name, job) for name, job in per_user_jobs(s).items()})
    sched.start()
    return sched
