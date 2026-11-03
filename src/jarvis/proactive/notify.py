import asyncio
import logging

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from jarvis.voice.protocol import MAX_SPEAK_CHARS
from jarvis.voice.push import fcm_access_token, send_data_push, send_push

log = logging.getLogger(__name__)
TELEGRAM_MAX = 4000  # Telegram refuses 4096+; keep headroom


class Notifier:
    """One user's outbound messages for scheduled jobs. Both senders are injected so jobs test without a network."""

    def __init__(self, send_message, send_push_text, send_push_data=None):
        self._send_message = send_message
        self._send_push = send_push_text
        self._send_push_data = send_push_data

    async def telegram(self, text: str, undo_event_id: str | None = None) -> None:
        markup = None
        if undo_event_id:
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("Undo", callback_data=f"undo:{undo_event_id}")]])
        await self._send_message(text[:TELEGRAM_MAX], markup)

    async def push(self, text: str) -> int:
        return await self._send_push(text[:MAX_SPEAK_CHARS])

    async def push_data(self, title: str, body: str, data: dict) -> int:
        """A silent-to-the-ear push: a notification that opens a screen, never speaks (no `speak` in the payload)."""
        return await self._send_push_data(title, body, data) if self._send_push_data else 0

    async def both(self, text: str, undo_event_id: str | None = None) -> None:
        """Push to the user's phones, and Telegram (with the Undo button) for the owner."""
        for send in (lambda: self.telegram(text, undo_event_id), lambda: self.push(text)):
            try:
                await send()
            except Exception as e:
                log.error("proactive delivery failed: %s", type(e).__name__)


def make_notifier(bot, chat_id: int, fcm_credentials_path: str, devices, user_id: int, telegram: bool) -> Notifier:
    """Push goes to the user's own devices; Telegram is the owner's chat only, a no-op for everyone else."""
    async def send_message(text, markup):
        if telegram:
            await bot.send_message(chat_id, text, reply_markup=markup)

    async def deliver(send) -> int:
        """`send(project, access, tokens, client)` on a worker thread, to this user's devices only."""
        if not fcm_credentials_path:
            return 0
        tokens = await asyncio.to_thread(devices.fcm_tokens, user_id)
        if not tokens:
            return 0

        def go() -> int:
            project, access = fcm_access_token(fcm_credentials_path)
            with httpx.Client(timeout=10) as client:
                return send(project, access, tokens, client)

        return await asyncio.to_thread(go)

    async def send_push_text(text) -> int:
        return await deliver(lambda project, access, tokens, client: send_push(project, access, tokens, text, client))

    async def send_push_data(title, body, data) -> int:
        return await deliver(lambda project, access, tokens, client:
                             send_data_push(project, access, tokens, title, body, data, client))

    return Notifier(send_message, send_push_text, send_push_data)
