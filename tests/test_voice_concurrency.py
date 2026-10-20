"""Voice and Telegram share one graph thread: graph steps are never cut mid-flight and never interleave."""
import asyncio
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from jarvis.agent.graph import repair_tool_gaps, window
from jarvis.channels.telegram import TelegramChannel
from jarvis.tenant import thread_config
from jarvis.voice.ws import HANDLED_TEXT

VOICE_CFG = thread_config(1, voice=True)
from tests.test_graph import call, tool
from tests.test_telegram import OWNER, button_update, chat, ids, sent, text_update
from tests.voice_helpers import AUTH, FINAL, build, ping, read, texts, until, until_state

OK = '{"ok": true}'


def blocking_tool(calls):
    """A gated create_event whose body blocks (in its worker thread) until the test releases it."""
    entered, release = threading.Event(), threading.Event()

    def fn(**kw):
        calls.append(("create_event", kw))
        entered.set()
        assert release.wait(10)
        return {"ok": True}

    return tool("create_event", "calendar", calls, fn=fn), entered, release


def gym_scripts(*more):
    return {"fast": [AIMessage("calendar"), call("create_event", {"summary": "Gym"}), AIMessage("Created."), *more],
            "strong": [AIMessage("Created.")]}


def present_card(ws):
    read(ws)
    ping(ws)
    return texts(until_state(ws, "listening"), "confirm_card")[0]


def tap(ws, card, decision="yes"):
    ws.send_json({"type": "confirm", "decision": decision, "interrupt_id": card["interrupt_id"]})


def settle(c, h):
    """Wait for every in-flight graph step, including abandoned ones."""
    async def wait():
        await asyncio.gather(*list(getattr(h.svc, "steps", ())), return_exceptions=True)
        await asyncio.sleep(0.05)
    c.portal.call(wait)


def model_view(c, h):
    """What the next agent call is fed (minus its system prompt)."""
    return repair_tool_gaps(window(c.portal.call(h.graph.aget_state, VOICE_CFG).values["messages"]))


def assert_ran_once_and_audited(c, h, calls):
    assert calls == [("create_event", {"summary": "Gym"})]
    rec = [r for r in h.audit.records if r["name"] == "create_event"]
    assert [r["confirmation"] for r in rec] == ["approved"]
    assert [m.content for m in model_view(c, h) if isinstance(m, ToolMessage)] == [OK]  # not "Not executed"


# --- C1: a running graph step always completes ---
def test_final_utterance_and_cancel_mid_tool_do_not_cut_the_step():
    calls = []
    t, entered, release = blocking_tool(calls)
    h = build(gym_scripts(AIMessage("chat"), AIMessage("You created Gym.")), [t],
              [FINAL("put gym at seven"), FINAL("thanks"), FINAL("what did you do")])
    with TestClient(h.app) as c:
        try:
            with c.websocket_connect("/voice", headers=AUTH) as ws:
                card = present_card(ws)
                tap(ws, card)
                assert entered.wait(5)
                ping(ws)  # "thanks" arrives while the tool is running
                until(ws, "transcript")
                ws.send_json({"type": "cancel"})
                until_state(ws, "listening")
                release.set()
                settle(c, h)
                ping(ws)  # next turn
                until_state(ws, "listening")
                assert "You created Gym." in h.tts.spoken
                assert "Created." not in h.tts.spoken  # the abandoned result is not spoken
        finally:
            release.set()
        assert_ran_once_and_audited(c, h, calls)


def test_socket_drop_mid_tool_does_not_cut_the_step():
    calls = []
    t, entered, release = blocking_tool(calls)
    h = build(gym_scripts(), [t], [FINAL("put gym at seven")])
    with TestClient(h.app) as c:
        try:
            with c.websocket_connect("/voice", headers=AUTH) as ws:
                tap(ws, present_card(ws))
                assert entered.wait(5)
            c.portal.call(asyncio.sleep, 0.2)  # the session has torn down while the tool still runs
            assert h.svc.sessions == {}
            release.set()
            settle(c, h)
        finally:
            release.set()
        assert_ran_once_and_audited(c, h, calls)


def test_replacement_mid_tool_does_not_cut_the_step():
    calls = []
    t, entered, release = blocking_tool(calls)
    h = build(gym_scripts(), [t], [FINAL("put gym at seven")])
    with TestClient(h.app) as c:
        first = c.websocket_connect("/voice", headers=AUTH)
        try:
            w1 = first.__enter__()
            tap(w1, present_card(w1))
            assert entered.wait(5)
            with c.websocket_connect("/voice", headers=AUTH) as w2:
                read(w2)
                with pytest.raises(WebSocketDisconnect) as e:
                    for _ in range(10):
                        read(w1)
                assert e.value.code == 4000
                first.__exit__(None, None, None)  # w1's handler unwinds once its client side has left
                c.portal.call(asyncio.sleep, 0.2)
                assert list(h.svc.sessions) == [1]  # w2 is the live session
                release.set()
                settle(c, h)
        finally:
            release.set()
        assert_ran_once_and_audited(c, h, calls)


# --- I1: one lock for both channels ---
class SlowReads:
    """Widens the read-pending -> resume window so unserialised confirmations would overlap."""

    def __init__(self, g):
        self.g = g

    async def aget_state(self, cfg):
        s = await self.g.aget_state(cfg)
        await asyncio.sleep(0.2)
        return s

    async def ainvoke(self, inp, cfg):
        return await self.g.ainvoke(inp, cfg)


def test_concurrent_voice_tap_and_telegram_button_run_the_tool_once():
    calls = []
    h = build(gym_scripts(), [tool("create_event", "calendar", calls)], [FINAL("put gym at seven")])
    slow = SlowReads(h.graph)
    h.rt.graph = slow
    ch = TelegramChannel(h.tenants, 1, OWNER, users=h.tenants.users)
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        card = present_card(ws)
        tc = chat()
        tap(ws, card)
        c.portal.call(ch.on_button, button_update(tc, f"yes:{card['interrupt_id']}"), None)
        until_state(ws, "listening")
        assert calls == [("create_event", {"summary": "Gym"})]
        losers = [sent(tc) == [(HANDLED_TEXT, {})], h.tts.spoken[-1] == HANDLED_TEXT]
        assert sorted(losers) == [False, True]


def test_telegram_text_waits_for_a_running_voice_step():
    calls = []
    t, entered, release = blocking_tool(calls)
    h = build(gym_scripts(AIMessage("chat"), AIMessage("Hi from Telegram.")), [t], [FINAL("put gym at seven")])
    ch = TelegramChannel(h.tenants, 1, OWNER, users=h.tenants.users)
    with TestClient(h.app) as c:
        try:
            with c.websocket_connect("/voice", headers=AUTH) as ws:
                tap(ws, present_card(ws))
                assert entered.wait(5)
                tc = chat()
                fut = c.portal.start_task_soon(ch.on_text, text_update(tc, "hello"), None)
                c.portal.call(asyncio.sleep, 0.3)
                assert not fut.done()  # held at the lock while the voice step runs
                release.set()
                fut.result(timeout=5)
                until_state(ws, "listening")
        finally:
            release.set()
        assert sent(tc) == [("Hi from Telegram.", {})]
        view = model_view(c, h)
        assert [type(m).__name__ for m in view] == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage",
                                                    "HumanMessage", "AIMessage"]
        assert view[2].content == OK and view[4].content == "hello"
        assert_ran_once_and_audited(c, h, calls)


def test_telegram_shared_thread_with_owner_voice():
    calls = []
    h = build(gym_scripts(), [tool("create_event", "calendar", calls)], [FINAL("hello")])
    ch = TelegramChannel(h.tenants, 1, OWNER, users=h.tenants.users)
    with TestClient(h.app) as c:
        tc = chat()
        c.portal.call(ch.on_text, text_update(tc, "put gym at seven"), None)  # proposed on Telegram
        iid = ids(tc)[0].partition(":")[2]
        with c.websocket_connect("/voice", headers=AUTH) as ws:
            read(ws)
            ping(ws)
            seen = until_state(ws, "listening")
            assert texts(seen, "confirm_card")[0]["interrupt_id"] == iid  # voice sees the same pending card
            assert calls == []
    assert h.tenants.lock(1) is h.rt.lock  # both channels take the same per-user lock


# --- I2: a spoken yes/no only answers a card this session presented ---
def test_spoken_yes_does_not_approve_a_card_this_session_never_presented():
    calls = []
    h = build(gym_scripts(), [tool("create_event", "calendar", calls)], [FINAL("yes"), FINAL("yes")])
    with TestClient(h.app) as c:
        out = c.portal.call(h.graph.ainvoke, {"messages": [HumanMessage("put gym at seven")]}, VOICE_CFG)
        iid = out["__interrupt__"][0].id
        with c.websocket_connect("/voice", headers=AUTH) as ws:
            read(ws)
            ping(ws)
            seen = until_state(ws, "listening")
            assert calls == []
            assert texts(seen, "confirm_card")[0]["interrupt_id"] == iid
            assert h.tts.spoken[-1].endswith("Just say yes or no.")
            ping(ws)  # now it was presented: a yes resumes it
            until_state(ws, "listening")
            assert calls == [("create_event", {"summary": "Gym"})] and "Created." in h.tts.spoken


def test_spoken_yes_meant_for_a_card_resolved_elsewhere_does_not_approve_the_next_one():
    calls = []
    h = build({"fast": [AIMessage("calendar, tasks"), call("create_event", {"summary": "Gym"}, id="c1"), AIMessage("cal done")],
               "strong": [call("create_task", {"summary": "Mum"}, id="c2")]},
              [tool("create_event", "calendar", calls), tool("create_task", "tasks", calls)],
              [FINAL("gym and remind me about mum"), FINAL("yes")])
    ch = TelegramChannel(h.tenants, 1, OWNER, users=h.tenants.users)
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        card_a = present_card(ws)
        tc = chat()
        c.portal.call(ch.on_button, button_update(tc, f"yes:{card_a['interrupt_id']}"), None)
        assert calls == [("create_event", {"summary": "Gym"})] and "create_task" in sent(tc)[-1][0]
        card_b_id = ids(tc)[0].partition(":")[2]
        ping(ws)  # "yes", meant for card A
        seen = until_state(ws, "listening")
        assert calls == [("create_event", {"summary": "Gym"})]
        assert texts(seen, "confirm_card")[0]["interrupt_id"] == card_b_id


@pytest.mark.parametrize("kind", ["utterance", "confirm"])
@pytest.mark.parametrize("deny", ["revoke", "disable"])
def test_queued_voice_step_rechecks_authorization_before_graph_access(pool, kind, deny):
    from unittest.mock import AsyncMock
    from jarvis.users import UserStore
    from jarvis.voice.devices import Devices

    users = UserStore(pool)
    uid = users.add("owner@example.com", "Europe/Berlin", is_owner=True).id
    calls = []
    h = build(gym_scripts(), [tool("create_event", "calendar", calls)], [FINAL("put gym at seven")])
    h.svc.devices = Devices(pool)
    did, token = h.svc.devices.create(uid)

    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                self.queued.set()
            return await super().acquire()

    lock = ObservedLock()
    lock.queued = asyncio.Event()
    h.tenants._locks[uid] = lock
    h.rt.lock = lock
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers={"authorization": f"Bearer {token}"}) as ws:
        if kind == "confirm":
            card = present_card(ws)
        else:
            read(ws)
        # Observe the public graph seam after setup: denied work must not even read pending state.
        state = AsyncMock(wraps=h.graph.aget_state)
        invoke = AsyncMock(wraps=h.graph.ainvoke)
        h.graph.aget_state, h.graph.ainvoke = state, invoke
        c.portal.call(lock.acquire)
        try:
            if kind == "confirm":
                tap(ws, card)
            else:
                ping(ws)
            c.portal.call(asyncio.wait_for, lock.queued.wait(), 5)
            if deny == "disable":
                users.set_disabled(uid, True)
            else:
                h.svc.devices.revoke(did)
        finally:
            c.portal.call(lock.release)
        close_code = None
        try:
            for _ in range(20):
                k, frame = read(ws)
                if k == "text" and frame == {"type": "state", "state": "listening"}:
                    break
        except WebSocketDisconnect as e:
            close_code = e.code
        assert close_code == 4401
        state.assert_not_awaited()
        invoke.assert_not_awaited()
        assert calls == []
        assert not lock.locked()


@pytest.mark.parametrize("outcome", ["cancel", "error"])
def test_voice_verification_interrupted_releases_lock_without_graph_access(caplog, outcome):
    from unittest.mock import AsyncMock

    h = build(gym_scripts(), stt_script=[FINAL("book gym")])
    entered, release = threading.Event(), threading.Event()
    verify = h.devices.verify
    count = 0

    def gated_verify(token):
        nonlocal count
        count += 1
        if count > 1:
            entered.set()
            assert release.wait(5)
            if outcome == "error":
                raise RuntimeError("synthetic-device-credential")
        return verify(token)

    h.devices.verify = gated_verify
    state = AsyncMock(wraps=h.graph.aget_state)
    h.graph.aget_state = state
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
        ping(ws)
        try:
            assert entered.wait(5)
            if outcome == "cancel":
                ws.send_json({"type": "cancel"})
                until_state(ws, "listening")
            else:
                release.set()
                code = None
                try:
                    for _ in range(20):
                        k, frame = read(ws)
                        if k == "text" and frame == {"type": "state", "state": "listening"}:
                            break
                except WebSocketDisconnect as e:
                    code = e.code
                assert code == 4401
            assert not h.tenants.lock(1).locked()
            state.assert_not_awaited()
            assert "synthetic-device-credential" not in caplog.text
        finally:
            release.set()
