import asyncio
import logging

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from jarvis.voice.protocol import MAX_SPEAK_CHARS
from jarvis.voice.push import fcm_access_token, send_push

log = logging.getLogger(__name__)
TELEGRAM_MAX = 4000  # Telegram refuses 4096+; keep headroom


class Notifier:
    """Owner-only outbound messages for scheduled jobs. Both senders are injected so jobs test without a network."""

    def __init__(self, send_message, send_push_text):
        self._send_message = send_message
        self._send_push = send_push_text

    async def telegram(self, text: str, undo_event_id: str | None = None) -> None:
        markup = None
        if undo_event_id:
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("Undo", callback_data=f"undo:{undo_event_id}")]])
        await self._send_message(text[:TELEGRAM_MAX], markup)

    async def push(self, text: str) -> int:
        return await self._send_push(text[:MAX_SPEAK_CHARS])

    async def both(self, text: str) -> None:
        for send in (self.telegram, self.push):
            try:
                await send(text)
            except Exception:
                log.exception("proactive delivery failed")


def make_notifier(bot, chat_id: int, fcm_credentials_path: str, devices) -> Notifier:
    async def send_message(text, markup):
        await bot.send_message(chat_id, text, reply_markup=markup)

    async def send_push_text(text) -> int:
        if not fcm_credentials_path:
            return 0
        tokens = await asyncio.to_thread(devices.fcm_tokens)
        if not tokens:
            return 0

        def go() -> int:
            project, access = fcm_access_token(fcm_credentials_path)
            with httpx.Client(timeout=10) as client:
                return send_push(project, access, tokens, text, client)

        return await asyncio.to_thread(go)

    return Notifier(send_message, send_push_text)
