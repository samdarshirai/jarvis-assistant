import asyncio
import json
import logging

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from jarvis.agent.graph import turn_replies
from jarvis.llm import MemoryContextError
from jarvis.tenant import UnknownUser, thread_config
from jarvis.users import UserStore

log = logging.getLogger(__name__)

FAIL_TEXT = "Something went wrong. Please try again."
EMPTY_TEXT = "Finished, but I have no summary to show. Ask me to check if you are unsure."
PENDING_TEXT = "Let's sort out the pending action first, confirm or cancel it."
RAW_CAP, CARD_MAX = 1500, 4000
HANDLED_TEXT = "Already handled."
DISABLED_TEXT = "This account is disabled."
NEEDS_LLM_TEXT = "Set your LLM key in the app Settings."
UNDO_DONE_TEXT = "Removed it from your calendar."
UNDO_FAILED_TEXT = "Could not remove it. Please check your calendar."
PHONE_OFFLINE_TEXT = "Phone action not run: the Jarvis voice app is not connected."
WARN_UNTRUSTED = "⚠ Proposed after reading third-party content (email or web) — check recipient and text.\n"


def keyboard(interrupt_id: str) -> InlineKeyboardMarkup:
    # buttons are bound to the interrupt they were shown for, so a stale or double tap cannot approve a later one
    return InlineKeyboardMarkup([[InlineKeyboardButton("Confirm", callback_data=f"yes:{interrupt_id}"),
                                  InlineKeyboardButton("Cancel", callback_data=f"no:{interrupt_id}")]])


def _clip(s: str, cap: int) -> str:
    return s if len(s) <= cap else s[:cap] + f"… ({len(s)} chars total)"


def format_confirmation(payload: dict) -> str:
    # Telegram rejects messages over 4096 chars and an unsendable card wedges the pending interrupt: clip the raw args
    head = WARN_UNTRUSTED if payload.get("after_untrusted") else ""

    def build(cap):
        lines = []
        for a in payload["actions"]:
            raw = _clip(f"{a['tool']}: {json.dumps(a['args'], ensure_ascii=False)}", cap)
            lines.append(f"• {a['summary']}\n  ({raw})" if a.get("summary") else f"• {raw}")
        return head + "Confirm this action?\n" + "\n".join(lines)

    cap = RAW_CAP
    while cap > 0 and len(build(cap)) > CARD_MAX:  # ponytail: halving; summaries are short so this ends fast
        cap //= 2
    card = build(cap)
    # hard clamp: summaries and the action count are unbounded; Telegram counts UTF-16 units, so cut there and drop a split pair
    if len(card.encode("utf-16-le")) // 2 >= CARD_MAX:
        card = card.encode("utf-16-le")[: 2 * (CARD_MAX - 1)].decode("utf-16-le", errors="ignore") + "…"
    return card


class TelegramChannel:
    def __init__(self, tenants, owner_user_id: int, owner_chat_id: int, deliver_actions=None, undo=None, *, users=None):
        self.deliver_actions = deliver_actions  # async (actions) -> bool; the owner's live voice session
        self.undo = undo  # async (event_id) -> bool; removes an event Jarvis auto-created from email
        self.tenants, self.owner_id = tenants, owner_user_id  # Telegram is the owner's channel
        self.users = users if users is not None else UserStore(tenants.pool)
        self.cfg = thread_config(owner_user_id)
        self.owner = owner_chat_id
        self.owner_filter = filters.Chat(chat_id=owner_chat_id)

    @property
    def lock(self) -> asyncio.Lock:
        return self.tenants.lock(self.owner_id)  # shared with voice and the app: one graph step at a time on the thread

    async def _enabled(self, chat):
        # Runtime user records are cached; authorization must read the database at execution time.
        user = await asyncio.to_thread(self.users.get, self.owner_id)
        if user is None or user.disabled:
            await chat.send_message(DISABLED_TEXT)
            return False
        return True

    async def _graph(self, chat):
        """The owner's graph, resolved per update (it changes with their LLM key); None after telling them to set one."""
        try:
            graph = (await self.tenants.get(self.owner_id)).graph
        except UnknownUser:
            await chat.send_message(DISABLED_TEXT)
            return None
        if not await self._enabled(chat):
            return None
        if graph is None:
            await chat.send_message(NEEDS_LLM_TEXT)
        return graph

    def build(self, token: str) -> Application:
        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(self.owner_filter & filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_handler(MessageHandler(~self.owner_filter, self.on_stranger), group=1)
        return app

    async def on_stranger(self, update, context):
        chat = update.effective_chat
        log.warning("dropped message from non-owner chat %s", chat.id if chat else None)

    async def _invoke(self, graph, graph_input):
        """Run the graph (the caller holds the lock). None means the run failed."""
        try:
            return await graph.ainvoke(graph_input, {**self.cfg, "configurable": {**self.cfg["configurable"], "channel": "telegram"}})
        except MemoryContextError as error:
            return {"memory_error": str(error)}
        except Exception:
            log.error("graph run failed")
            return None

    async def _offer(self, chat, it) -> None:
        await chat.send_message(format_confirmation(it.value), reply_markup=keyboard(it.id))

    async def _reply(self, chat, graph, result) -> None:
        if result is None or result.get("memory_error"):
            await chat.send_message(result["memory_error"] if result else FAIL_TEXT)
            try:  # buttons may be gone; re-offer a still-pending confirmation so the owner is not locked out
                state = await graph.aget_state(self.cfg)
                if state.interrupts:
                    await self._offer(chat, state.interrupts[0])
            except Exception:
                log.error("could not re-offer pending confirmation")
            return
        interrupts = result.get("__interrupt__")
        if interrupts:
            await self._offer(chat, interrupts[0])
            return
        for text in turn_replies(result["messages"]) or [EMPTY_TEXT]:
            await chat.send_message(text)
        if result.get("client_actions"):
            delivered = bool(self.deliver_actions) and await self.deliver_actions(result["client_actions"])
            if not delivered:
                await chat.send_message(PHONE_OFFLINE_TEXT)

    async def on_text(self, update, context):
        chat = update.effective_chat
        async with self.lock:  # authorization -> read pending -> decide -> run; replies follow release
            graph = await self._graph(chat)
            if graph is None:
                return
            pending = (await graph.aget_state(self.cfg)).interrupts
            result = None if pending else await self._invoke(graph, {"messages": [HumanMessage(update.message.text)]})
        if pending:
            await chat.send_message(PENDING_TEXT)
            await self._offer(chat, pending[0])  # its buttons may only exist elsewhere (voice): show them here
            return
        await self._reply(chat, graph, result)

    async def _undo(self, chat, event_id: str) -> None:
        try:
            done = bool(self.undo) and await self.undo(event_id)
        except Exception:
            log.error("undo failed")
            await chat.send_message(UNDO_FAILED_TEXT)
            return
        await chat.send_message(UNDO_DONE_TEXT if done else HANDLED_TEXT)

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
            log.warning("could not remove confirmation buttons")
        action, _, iid = (q.data or "").partition(":")
        async with self.lock:
            if action == "undo":  # no graph step; still authorize after waiting for the owner's lock
                if await self._enabled(chat):
                    await self._undo(chat, iid)
                return
            graph = await self._graph(chat)
            if graph is None:
                return
            interrupts = (await graph.aget_state(self.cfg)).interrupts
            stale = action not in ("yes", "no") or not interrupts or interrupts[0].id != iid
            result = None if stale else await self._invoke(graph, Command(resume=action == "yes"))
        if stale:
            await chat.send_message(HANDLED_TEXT)
            return
        await self._reply(chat, graph, result)
