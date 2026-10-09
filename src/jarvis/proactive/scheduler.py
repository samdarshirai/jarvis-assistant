import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from jarvis.google.auth import ReauthRequired
from jarvis.proactive.alerts import sweep
from jarvis import weather
from jarvis.proactive.brief import run_brief
from jarvis.proactive.mailwatch import MailWatch
from jarvis.proactive.notify import make_notifier
from jarvis.timeutil import now_local

log = logging.getLogger(__name__)
REAUTH_TEXT = "Jarvis needs Google access again. Run python -m jarvis.google.auth on the server."


def guarded(name: str, job, notifier, store, audit, tz: str):
    """A job wrapper that never raises: one failing job must not stop the scheduler or the other jobs."""
    async def run():
        try:
            await job()
        except ReauthRequired:
            try:
                day = now_local(tz).date().isoformat()
                if await asyncio.to_thread(store.claim_alert, f"reauth:{day}"):
                    await notifier.telegram(REAUTH_TEXT)
            except Exception:
                log.exception("could not send the re-consent notice")
        except Exception as e:
            log.exception("proactive job %s failed", name)
            try:
                await asyncio.to_thread(audit.record, "proactive", f"{name}_error", result={"error": str(e)[:200]})
            except Exception:
                log.exception("could not audit the failure")
    return run


def build_scheduler(s, jobs: dict) -> AsyncIOScheduler:
    # in-memory jobstore on purpose: jobs are re-registered at startup and all durable state is in Postgres
    sched = AsyncIOScheduler(timezone=s.timezone)
    common = dict(coalesce=True, max_instances=1)
    if s.brief_enabled:
        hour, minute = (int(p) for p in s.brief_time.split(":"))
        sched.add_job(jobs["brief"], CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=s.timezone),
                      id="brief", misfire_grace_time=3600, **common)
    sched.add_job(jobs["mail"], IntervalTrigger(minutes=s.mail_poll_minutes), id="mail", misfire_grace_time=60, **common)
    sched.add_job(jobs["sweep"], IntervalTrigger(minutes=5), id="sweep", misfire_grace_time=60, **common)
    return sched


def start_proactive(s, store, calendar, tasks, gmail, llm, audit, bot, devices) -> AsyncIOScheduler:
    notifier = make_notifier(bot, s.telegram_owner_chat_id, s.fcm_credentials_path, devices)
    watch = MailWatch(gmail, calendar, store, notifier, llm, audit, s.timezone, s.auto_event_cap)
    raw = {
        "brief": lambda: run_brief(calendar, tasks, gmail, llm, notifier, audit, s.timezone, weather_city=s.weather_city,
                           location=lambda: weather.load_coords(store)),
        "mail": lambda: watch.run_once(),
        "sweep": lambda: sweep(calendar, store, notifier, s.timezone, s.leave_lead_minutes),
    }
    sched = build_scheduler(s, {k: guarded(k, j, notifier, store, audit, s.timezone) for k, j in raw.items()})
    sched.start()
    return sched
