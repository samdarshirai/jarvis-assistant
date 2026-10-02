import asyncio
import contextlib
import json
import logging

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from starlette.websockets import WebSocket

from jarvis.agent.graph import turn_replies
from jarvis.channels.telegram import EMPTY_TEXT, FAIL_TEXT, THREAD
from jarvis.voice import protocol as P
from jarvis.voice.confirm import match_confirmation
from jarvis.voice.tts import split_sentences

log = logging.getLogger(__name__)

# One thread for every channel (FR-1). The "voice" flag only selects the fast model tier inside the graph.
VOICE_CFG = {**THREAD, "configurable": {**THREAD["configurable"], "voice": True}}
TAP_ONLY = {"send_draft"}  # irreversible and third-party-facing: a spoken yes is never enough
PENDING_TEXT = "Confirm or cancel the pending action first."
TAP_TEXT = "Tap Confirm on the screen to send."
STT_DOWN_TEXT = "I can't hear you right now."
WARN_TEXT = "Warning: proposed after reading email content. "
HANDLED_TEXT = "Already handled."


def card_lines(payload: dict) -> list[str]:
    return [a.get("summary") or f"{a['tool']}: {json.dumps(a['args'], ensure_ascii=False)}" for a in payload["actions"]]


class VoiceSession:
    def __init__(self, ws: WebSocket, graph, devices, device_id: int, stt, tts):
        self.ws, self.graph, self.devices, self.device_id, self.stt, self.tts = ws, graph, devices, device_id, stt, tts
        self.state = "listening"
        self.turn: asyncio.Task | None = None
        self.utterance_bytes = 0

    # --- output ---
    async def send(self, type_: str, **fields) -> bool:
        try:
            await self.ws.send_text(P.frame(type_, **fields))
            return True
        except Exception:  # socket already gone
            return False

    async def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            if state == "listening":
                self.utterance_bytes = 0
            await self.send("state", state=state)

    async def close(self, code: int) -> None:
        with contextlib.suppress(Exception):
            await self.ws.close(code=code)

    async def _say(self, texts: list[str]) -> None:
        sentences = [s for t in texts for s in split_sentences(t)]
        for t in texts:
            await self.send("transcript", role="assistant", text=t, final=True)
        if sentences:
            await self.set_state("speaking")
            try:
                async for chunk in self.tts.synth(sentences):
                    await self.ws.send_bytes(chunk)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tts failed")
                await self.send("error", message="Speech output failed; the reply is in the transcript.")
        await self.set_state("listening")

    # --- turn control ---
    async def _stop_turn(self) -> None:
        t, self.turn = self.turn, None
        if t and not t.done():
            t.cancel()
            await asyncio.wait({t})  # inner outcome is not raised; our own cancellation still propagates

    async def _start(self, coro) -> None:
        await self._stop_turn()
        self.turn = asyncio.create_task(self._guarded(coro))

    async def _guarded(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._failed()

    async def _failed(self) -> None:
        log.exception("voice turn failed")
        await self.send("error", message=FAIL_TEXT)
        await self._say([FAIL_TEXT])
        with contextlib.suppress(Exception):  # buttons may be gone: re-offer a still-pending confirmation
            state = await self.graph.aget_state(VOICE_CFG)
            if state.interrupts:
                await self._card(state.interrupts[0])

    async def _utterance(self, text: str) -> None:
        await self.set_state("thinking")
        pending = (await self.graph.aget_state(VOICE_CFG)).interrupts
        if pending:
            await self._spoken_decision(text, pending[0])
            return
        await self._invoke({"messages": [HumanMessage(text)]})

    async def _spoken_decision(self, text: str, it) -> None:
        decision = match_confirmation(text)
        tap_only = any(a["tool"] in TAP_ONLY for a in it.value["actions"])
        if decision is None or (decision and tap_only):
            await self._card(it)  # re-show the card; nothing was decided
            await self._say([TAP_TEXT if decision else PENDING_TEXT])
            return
        await self._invoke(Command(resume=decision))

    async def _speak_text(self, m: dict) -> None:
        text = m.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > P.MAX_SPEAK_CHARS:
            await self.send("error", message="speak needs text of 1 to 2000 characters.")
            return
        await self._say([text])

    async def _tap(self, m: dict) -> None:
        await self.set_state("thinking")
        decision = m.get("decision")
        interrupts = (await self.graph.aget_state(VOICE_CFG)).interrupts
        if decision not in ("yes", "no") or not interrupts or interrupts[0].id != m.get("interrupt_id"):
            await self._say([HANDLED_TEXT])  # stale, repeated or bare taps never resume anything
            return
        await self._invoke(Command(resume=decision == "yes"))

    async def _invoke(self, graph_input) -> None:
        result = await self.graph.ainvoke(graph_input, VOICE_CFG)
        await self._present(result)

    async def _present(self, result: dict) -> None:
        interrupts = result.get("__interrupt__")
        if interrupts:
            await self._offer(interrupts[0])
            return
        if result.get("client_actions"):  # before the speech, so an alarm is set while Jarvis is still talking
            await self.send("client_actions", actions=result["client_actions"])
        await self._say(turn_replies(result["messages"]) or [EMPTY_TEXT])

    # --- confirmation cards (spoken-yes handling is added in Task 6) ---
    async def _card(self, it) -> None:
        payload = it.value
        await self.send("confirm_card", interrupt_id=it.id, summary="\n".join(card_lines(payload)),
                        tap_only=any(a["tool"] in TAP_ONLY for a in payload["actions"]),
                        after_untrusted=bool(payload.get("after_untrusted")))

    async def _offer(self, it) -> None:
        await self._card(it)
        payload = it.value
        tap_only = any(a["tool"] in TAP_ONLY for a in payload["actions"])
        spoken = (WARN_TEXT if payload.get("after_untrusted") else "") + "; ".join(card_lines(payload))
        await self._say([spoken + (". " + TAP_TEXT if tap_only else ". Say yes or no.")])

    # --- input ---
    async def _consume_events(self) -> None:
        try:
            async for ev in self.stt.events():
                if ev.kind == "partial":
                    await self.send("transcript", role="user", text=ev.text, final=False)  # first: the app flushes on it
                    if self.state == "speaking":  # speech over playback = barge-in
                        await self._stop_turn()
                        await self.set_state("listening")
                else:
                    self.utterance_bytes = 0
                    await self.send("transcript", role="user", text=ev.text, final=True)
                    await self._start(self._utterance(ev.text))
            # a clean end of the stream is a failure too (cancellation raises instead, so teardown never lands here)
            log.error("stt stream ended unexpectedly")
            await self._stt_failed()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stt failed")
            await self._stt_failed()

    async def _stt_failed(self) -> None:
        await self.send("error", message="Speech recognition is unavailable.")
        with contextlib.suppress(Exception):
            await self._say([STT_DOWN_TEXT])
        await self.close(P.CLOSE_UPSTREAM)

    async def _audio(self, data: bytes) -> bool:
        if len(data) > P.MAX_AUDIO_FRAME:
            await self.send("error", message="Audio frame too large.")
            await self.close(P.CLOSE_TOO_BIG)
            return False
        if self.state == "listening":  # the cap bounds how long the mic can stay open, not reply playback
            self.utterance_bytes += len(data)
            if self.utterance_bytes > P.MAX_UTTERANCE_BYTES:
                await self.send("error", message="Utterance too long.")
                await self.close(P.CLOSE_TOO_LONG)
                return False
        await self.stt.send(data)
        return True

    async def _control(self, text: str) -> bool:
        m = P.parse(text)
        if m is None:
            await self.send("error", message="Bad frame.")
            return True
        kind = m["type"]
        if kind == "hello":
            fcm = m.get("fcm_token")
            if isinstance(fcm, str) and fcm:
                await asyncio.to_thread(self.devices.set_fcm, self.device_id, fcm)
        elif kind == "confirm":
            await self._start(self._tap(m))
        elif kind == "cancel":
            await self._stop_turn()
            await self.set_state("listening")
        elif kind == "speak":
            await self._start(self._speak_text(m))
        elif kind == "bye":
            return False
        else:
            await self.send("error", message=f"Unknown frame type: {kind}")
        return True

    async def run(self) -> None:
        events = asyncio.create_task(self._consume_events())
        try:
            await self.send("state", state="listening")
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    if not await self._audio(msg["bytes"]):
                        break
                elif msg.get("text") is not None:
                    if not await self._control(msg["text"]):
                        break
        except RuntimeError:  # receive() after the peer closed
            pass
        finally:
            events.cancel()
            try:  # nested so a cancellation of run() itself still releases the turn and the stt stream
                await asyncio.gather(events, return_exceptions=True)  # no new turn can start after this
            finally:
                try:
                    await self._stop_turn()
                finally:
                    with contextlib.suppress(Exception):
                        await self.stt.close()


class VoiceService:
    def __init__(self, graph, devices, stt, tts):
        self.graph, self.devices, self.stt, self.tts = graph, devices, stt, tts
        self.current: VoiceSession | None = None

    async def handle(self, ws: WebSocket) -> None:
        if self.stt is None or self.tts is None:
            await ws.close(code=P.CLOSE_UNAVAILABLE)
            return
        auth = ws.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        device_id = await asyncio.to_thread(self.devices.verify, token) if token else None
        if device_id is None:
            await ws.close(code=P.CLOSE_UNAUTHORIZED)  # before accept: no audio is ever read from a stranger
            return
        await ws.accept()
        try:
            stream = await self.stt.open()
        except Exception:
            log.exception("could not open stt")
            await ws.send_text(P.frame("error", message="Speech recognition is unavailable."))
            await ws.close(code=P.CLOSE_UPSTREAM)
            return
        session = VoiceSession(ws, self.graph, self.devices, device_id, stream, self.tts)
        old, self.current = self.current, session
        if old:
            await old.close(P.CLOSE_REPLACED)
        try:
            await session.run()
        finally:
            if self.current is session:
                self.current = None
            with contextlib.suppress(Exception):
                await ws.close()

    async def deliver(self, actions: list[dict]) -> bool:
        s = self.current
        return bool(s) and await s.send("client_actions", actions=actions)
