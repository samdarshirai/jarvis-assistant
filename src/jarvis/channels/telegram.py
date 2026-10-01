import json
import logging

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from jarvis.agent.graph import turn_replies

log = logging.getLogger(__name__)

THREAD = {"configurable": {"thread_id": "owner"}, "recursion_limit": 40}
FAIL_TEXT = "Something went wrong. Please try again."
EMPTY_TEXT = "Finished, but I have no summary to show. Ask me to check if you are unsure."


def keyboard(interrupt_id: str) -> InlineKeyboardMarkup:
    # buttons are bound to the interrupt they were shown for, so a stale or double tap cannot approve a later one
    return InlineKeyboardMarkup([[InlineKeyboardButton("Confirm", callback_data=f"yes:{interrupt_id}"),
                                  InlineKeyboardButton("Cancel", callback_data=f"no:{interrupt_id}")]])


def format_confirmation(payload: dict) -> str:
    lines = []
    for a in payload["actions"]:
        raw = f"{a['tool']}: {json.dumps(a['args'], ensure_ascii=False)}"
        lines.append(f"• {a['summary']}\n  ({raw})" if a.get("summary") else f"• {raw}")
    return "Confirm this action?\n" + "\n".join(lines)


class TelegramChannel:
    def __init__(self, graph, owner_chat_id: int):
        self.graph = graph
        self.owner = owner_chat_id
        self.owner_filter = filters.Chat(chat_id=owner_chat_id)

    def build(self, token: str) -> Application:
        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(self.owner_filter & filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_handler(MessageHandler(~self.owner_filter, self.on_stranger), group=1)
        return app

    async def on_stranger(self, update, context):
        chat = update.effective_chat
        log.warning("dropped message from non-owner chat %s", chat.id if chat else None)

    async def _pending(self) -> bool:
        return bool((await self.graph.aget_state(THREAD)).interrupts)

    async def _run(self, chat, graph_input) -> None:
        try:
            result = await self.graph.ainvoke(graph_input, THREAD)
        except Exception:
            log.exception("graph run failed")
            await chat.send_message(FAIL_TEXT)
            try:  # buttons may be gone; re-offer a still-pending confirmation so the owner is not locked out
                state = await self.graph.aget_state(THREAD)
                if state.interrupts:
                    await chat.send_message(format_confirmation(state.interrupts[0].value),
                                            reply_markup=keyboard(state.interrupts[0].id))
            except Exception:
                log.exception("could not re-offer pending confirmation")
            return
        interrupts = result.get("__interrupt__")
        if interrupts:
            await chat.send_message(format_confirmation(interrupts[0].value), reply_markup=keyboard(interrupts[0].id))
            return
        for text in turn_replies(result["messages"]) or [EMPTY_TEXT]:
            await chat.send_message(text)

    async def on_text(self, update, context):
        chat = update.effective_chat
        if await self._pending():
            await chat.send_message("Confirm or cancel the pending action first.")
            return
        await self._run(chat, {"messages": [HumanMessage(update.message.text)]})

    async def on_button(self, update, context):
        q = update.callback_query
        await q.answer()
        if q.message.chat_id != self.owner:
            log.warning("dropped button tap from non-owner chat %s", q.message.chat_id)
            return
        chat = q.message.chat
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            log.warning("could not remove confirmation buttons", exc_info=True)
        action, _, iid = (q.data or "").partition(":")
        interrupts = (await self.graph.aget_state(THREAD)).interrupts
        if action not in ("yes", "no") or not interrupts or interrupts[0].id != iid:
            await chat.send_message("Already handled.")
            return
        await self._run(chat, Command(resume=action == "yes"))
