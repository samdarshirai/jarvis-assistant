import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from jarvis.kg.candidates import EXPIRE_AFTER, CandidateStore
from jarvis.kg.store import GraphStore
from jarvis.proactive.learned import WINDOW, announce
from jarvis.proactive.notify import make_notifier
from jarvis.proactive.scheduler import per_user_jobs
from jarvis.voice.push import send_data_push, send_push
from tests.fakes import FakeNotifier, FakeProactiveStore
from tests.test_kg_candidates import NOW, conv, q, row
from tests.test_proactive_scheduler import runtime, settings, user


def rt_with_store(store=None):
    return SimpleNamespace(proactive=store or FakeProactiveStore())


async def test_one_push_per_conversation_with_a_typed_silent_payload():
    rt, n = rt_with_store(), FakeNotifier()
    assert await announce(rt, n, 7, 3, NOW) is True
    assert n.calls == [("push_data", "Jarvis learned 3 things", "Open Memory to review.", {"type": "candidates", "count": 3})]
    assert all("speak" not in c[3] for c in n.calls)
    assert await announce(rt, n, 7, 3, NOW + 2 * WINDOW) is False  # the same conversation never pushes twice
    assert len(n.calls) == 1


async def test_singular_title():
    n = FakeNotifier()
    await announce(rt_with_store(), n, 1, 1, NOW)
    assert n.calls[0][1] == "Jarvis learned 1 thing"


async def test_at_most_one_push_per_thirty_minutes():
    rt, n = rt_with_store(), FakeNotifier()
    assert await announce(rt, n, 1, 2, NOW)
    assert not await announce(rt, n, 2, 1, NOW + WINDOW - timedelta(seconds=1))  # only the badge moves
    assert await announce(rt, n, 3, 1, NOW + WINDOW)
    assert [c[3]["count"] for c in n.calls] == [2, 1]


async def test_no_device_reached_does_not_start_the_window():
    rt, n = rt_with_store(), FakeNotifier()
    n.push_data = AsyncMock(return_value=0)
    assert not await announce(rt, n, 1, 2, NOW)
    n.push_data = AsyncMock(return_value=1)
    assert await announce(rt, n, 2, 2, NOW + timedelta(minutes=1))


async def test_a_failing_sender_never_raises_into_the_extractor(caplog):
    n = FakeNotifier()
    n.push_data = AsyncMock(side_effect=RuntimeError("boom"))
    assert await announce(rt_with_store(), n, 1, 2, NOW) is False
    assert "RuntimeError" in caplog.text and "boom" not in caplog.text


def test_data_push_has_no_speak_and_stringifies_data():
    posted = []

    def handler(request):
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert send_data_push("proj", "AT", ["t1", "t2"], "Jarvis learned 3 things", "Open Memory to review.",
                              {"type": "candidates", "count": 3}, c) == 2
        with pytest.raises(ValueError):
            send_data_push("proj", "AT", ["t1"], "x", "y", {"speak": "hi"}, c)
        assert send_push("proj", "AT", ["t1"], "hello", c) == 1
    first, speaking = posted[0]["message"], posted[2]["message"]
    assert first["data"] == {"type": "candidates", "count": "3"} and "speak" not in first["data"]
    assert first["notification"]["title"] == "Jarvis learned 3 things" and first["token"] == "t1"
    assert speaking["data"] == {"speak": "hello"}  # the existing speaking push is unchanged


async def test_make_notifier_sends_the_data_push_to_that_users_devices_only(monkeypatch):
    monkeypatch.setattr("jarvis.proactive.notify.fcm_access_token", lambda path: ("proj", "AT"))
    sent = []
    monkeypatch.setattr("jarvis.proactive.notify.send_data_push",
                        lambda project, access, tokens, title, body, data, client: sent.append((tokens, title, data)) or len(tokens))
    devices = SimpleNamespace(fcm_tokens=lambda uid: [f"phone-of-{uid}"])
    bot = SimpleNamespace(send_message=AsyncMock())
    assert await make_notifier(bot, 42, "creds.json", devices, 2, False).push_data("T", "B", {"type": "candidates", "count": 1}) == 1
    assert sent == [(["phone-of-2"], "T", {"type": "candidates", "count": 1})]
    bot.send_message.assert_not_called()
    assert await make_notifier(bot, 42, "", devices, 2, False).push_data("T", "B", {}) == 0  # no FCM credentials


async def test_the_extract_job_announces_through_the_notifier(monkeypatch):
    async def fake(rt, llm, now, on_candidates=None, digest=True):
        await on_candidates(11, 2)

    monkeypatch.setattr("jarvis.proactive.scheduler.run_extract", fake)
    rt, n = runtime(user(1)), FakeNotifier()
    await per_user_jobs(settings())["extract"](rt, n, None, "Europe/Berlin", NOW)
    assert [c[3] for c in n.calls] == [{"type": "candidates", "count": 2}]
    assert rt.proactive.alerts == {"candidates:11"}


async def test_the_expire_job_expires_only_the_users_old_candidates(pool, uids):
    mine, theirs = CandidateStore(pool, uids[0]), CandidateStore(pool, uids[1])
    for cs, uid in ((mine, uids[0]), (theirs, uids[1])):
        cid = conv(pool, uid, closed=True)
        assert cs.save(cid, NOW, [row()], NOW)
    q(pool, "UPDATE fact_candidate SET created_at = %s RETURNING 1", NOW - EXPIRE_AFTER - timedelta(days=1))
    rt = SimpleNamespace(kg=GraphStore(pool, uids[0]), user=user(uids[0]))
    await per_user_jobs(settings())["expire"](rt, FakeNotifier(), None, "Europe/Berlin", NOW)
    assert q(pool, "SELECT user_id, status FROM fact_candidate ORDER BY user_id") == [(uids[0], "expired"), (uids[1], "new")]
    await per_user_jobs(settings())["expire"](SimpleNamespace(kg=None, user=user(1)), FakeNotifier(), None, "UTC", NOW)  # no graph: no-op
