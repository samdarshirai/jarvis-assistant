"""The "Jarvis learned N things" push for the Learned inbox: one per conversation that produced candidates, at most one
every 30 minutes (later candidates only raise the app's badge). The payload is typed and has no `speak`, so tapping it
opens the Memory screen and never plays anything aloud. The server has no quiet-hours setting (the phone owns those)."""
import asyncio
import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)
WINDOW = timedelta(minutes=30)
LAST_PUSH = "candidates_push_at"  # proactive_state key: when the last Learned push reached a device


async def announce(rt, notifier, conv_id: int, count: int, now: datetime) -> bool:
    """Push for the candidates a conversation produced; True when a device got it. Never raises into the extractor."""
    try:
        if not await asyncio.to_thread(rt.proactive.claim_alert, f"candidates:{conv_id}"):
            return False  # this conversation already had its push
        last = await asyncio.to_thread(rt.proactive.get_state, LAST_PUSH)
        if last and now - datetime.fromisoformat(last) < WINDOW:
            return False  # too soon after the previous one: the badge carries it
        title = f"Jarvis learned {count} thing{'' if count == 1 else 's'}"
        sent = await notifier.push_data(title, "Open Memory to review.", {"type": "candidates", "count": count})
        if sent:
            await asyncio.to_thread(rt.proactive.set_state, LAST_PUSH, now.isoformat())
        return bool(sent)
    except Exception as e:
        log.error("could not announce the learned facts: %s", type(e).__name__)  # the type only
        return False
