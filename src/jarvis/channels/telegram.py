import json
import logging

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from jarvis.agent.graph import turn_replies

log = logging.getLogger(__name__)

THREAD = {"configurable": {"thread_id": "owner"}, "recursion_limit": 40}
FAIL_TEXT = "Something went wrong. Please try again."
KEYBOARD = InlineKeyboardMarkup([[InlineKeyboardButton("Confirm", callback_data="yes"),
                                  InlineKeyboardButton("Cancel", callback_data="no")]])


def format_confirmation(payload: dict) -> str:
    lines = [f"• {a['tool']}: {json.dumps(a['args'], ensure_ascii=False)}" for a in payload["actions"]]
    return "Confirm this action?\n" + "\n".join(lines)


class TelegramChannel:
    def __init__(self, graph, owner_chat_id: int):
        self.graph = graph
        self.owner = owner_chat_id
        self.owner_filter = filters.Chat(chat_id=owner_chat_id)

    def build(self, token: str) -> Application:
        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(self.owner_filter & filters.TEXT & ~filters.COMMAND, self.on_text))
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
            return
        interrupts = result.get("__interrupt__")
        if interrupts:
            await chat.send_message(format_confirmation(interrupts[0].value), reply_markup=KEYBOARD)
            return
        for text in turn_replies(result["messages"]):
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
            return
        chat = q.message.chat
        await q.edit_message_reply_markup(reply_markup=None)
        if not await self._pending():
            await chat.send_message("Already handled.")
            return
        await self._run(chat, Command(resume=q.data == "yes"))
