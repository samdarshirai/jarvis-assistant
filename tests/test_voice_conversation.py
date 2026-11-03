"""One voice connection is one conversation: its own thread, closed on every exit, titled by the first final."""
import asyncio

from fastapi.testclient import TestClient

from tests.voice_helpers import AUTH, FINAL, build, chat_scripts, ping, read, until_state, voice_cfg


def settle(c):
    c.portal.call(asyncio.sleep, 0.3)


def wait_until(c, cond, timeout=5.0):
    """Poll for a server-side teardown instead of guessing how long a slow runner needs."""
    async def poll():
        end = asyncio.get_running_loop().time() + timeout
        while not cond() and asyncio.get_running_loop().time() < end:
            await asyncio.sleep(0.05)
    c.portal.call(poll)


def voice_rows(h):
    return sorted((r for r in h.rt.conversations.rows.values() if r.surface == "voice"), key=lambda r: r.id)


def say(ws):
    read(ws)
    ping(ws)
    until_state(ws, "listening")


def test_each_connection_gets_its_own_thread():
    h = build(chat_scripts("One.", "chat", "Two."), stt_script=[FINAL("first"), FINAL("second")])
    with TestClient(h.app) as c:
        for _ in range(2):
            with c.websocket_connect("/voice", headers=AUTH) as ws:
                say(ws)
            settle(c)
        a, b = voice_rows(h)
        assert a.thread_id != b.thread_id
        view = lambda r: [m.content for m in c.portal.call(
            h.graph.aget_state, {"configurable": {"thread_id": r.thread_id}}).values["messages"]]
        assert view(a) == ["first", "One."] and view(b) == ["second", "Two."]


def test_conversation_closed_on_every_exit():
    h = build(chat_scripts("Hi."), stt_script=[])
    with TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as ws:  # (a) bye
            read(ws)
            ws.send_json({"type": "bye"})
        settle(c)
        with c.websocket_connect("/voice", headers=AUTH) as ws:  # (c) socket drop
            read(ws)
        settle(c)
        h.stt.fail_events = True
        with c.websocket_connect("/voice", headers=AUTH) as ws:  # (b) stt dies under the session
            read(ws)
            settle(c)
        wait_until(c, lambda: len(voice_rows(h)) == 3 and all(h.rt.conversations.get(r.id).closed_at for r in voice_rows(h)))
        rows = voice_rows(h)
        states = [(r.id, bool(h.rt.conversations.get(r.id).closed_at)) for r in rows]
        assert len(rows) == 3 and all(ok for _, ok in states), states


def test_replaced_session_does_not_close_newer_conversation():
    h = build(chat_scripts("Hi."))
    with TestClient(h.app) as c:
        with c.websocket_connect("/voice", headers=AUTH) as w1:
            read(w1)
            with c.websocket_connect("/voice", headers=AUTH) as w2:
                read(w2)
                settle(c)  # w1 is told CLOSE_REPLACED and its teardown runs
                first, second = voice_rows(h)
                assert h.rt.conversations.get(first.id).closed_at
                assert h.rt.conversations.get(second.id).closed_at is None
        wait_until(c, lambda: h.rt.conversations.get(second.id).closed_at)
        assert h.rt.conversations.get(second.id).closed_at


def test_title_from_first_final_transcript():
    h = build(chat_scripts("A.", "chat", "B."), stt_script=[FINAL("put gym at seven"), FINAL("and then")])
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        say(ws)
        ping(ws)
        until_state(ws, "listening")
        (conv,) = voice_rows(h)
        assert conv.title == "put gym at seven"


def test_rejected_connection_creates_no_conversation():
    h = build(chat_scripts("Hi."), with_voice=False)
    with TestClient(h.app) as c, c.websocket_connect("/voice", headers=AUTH) as ws:
        read(ws)
    assert voice_rows(h) == []
