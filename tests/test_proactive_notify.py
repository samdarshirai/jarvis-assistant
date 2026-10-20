import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from langchain_core.messages import AIMessage

from jarvis.proactive.notify import Notifier, make_notifier
from jarvis.proactive.alerts import sweep
from jarvis.proactive.mailwatch import MailWatch, event_id_for
from jarvis.voice.push import send_push
from tests.fakes import FakeProactiveStore, FakeProvider, MemoryAudit


async def test_telegram_clips_text_and_adds_undo_button():
    sent = []

    async def send(text, markup):
        sent.append((text, markup))

    n = Notifier(send, AsyncMock())
    await n.telegram("x" * 5000)
    await n.telegram("added", undo_event_id="abc")
    assert len(sent[0][0]) == 4000 and sent[0][1] is None
    button = sent[1][1].inline_keyboard[0][0]
    assert (button.text, button.callback_data) == ("Undo", "undo:abc")
    assert len(button.callback_data.encode()) <= 64


async def test_push_clips_to_the_speak_limit():
    push = AsyncMock(return_value=1)
    n = Notifier(AsyncMock(), push)
    assert await n.push("y" * 3000) == 1
    push.assert_awaited_once_with("y" * 2000)


async def test_both_sends_each_channel_even_if_telegram_fails():
    pushed = []

    async def boom(text, markup):
        raise RuntimeError("telegram down")

    async def push(text):
        pushed.append(text)
        return 1

    await Notifier(boom, push).both("brief")
    assert pushed == ["brief"]


async def test_make_notifier_sends_to_the_owner_chat():
    bot = SimpleNamespace(send_message=AsyncMock())
    n = make_notifier(bot, 42, "", SimpleNamespace(fcm_tokens=lambda uid: ["t"]), 1, True)
    await n.telegram("hi", undo_event_id="abc")
    args, kwargs = bot.send_message.call_args
    assert args == (42, "hi") and kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "undo:abc"


async def test_push_is_a_noop_without_credentials_or_devices():
    assert await make_notifier(SimpleNamespace(), 1, "", SimpleNamespace(fcm_tokens=lambda uid: ["t"]), 1, True).push("x") == 0
    assert await make_notifier(SimpleNamespace(), 1, "creds.json", SimpleNamespace(fcm_tokens=lambda uid: []), 1, True).push("x") == 0


async def test_push_sends_to_every_device_token(monkeypatch):
    seen = {}
    monkeypatch.setattr("jarvis.proactive.notify.fcm_access_token", lambda path: ("proj", "AT"))

    def fake_send(project, access, tokens, text, client):
        seen.update(project=project, access=access, tokens=tokens, text=text)
        return len(tokens)

    monkeypatch.setattr("jarvis.proactive.notify.send_push", fake_send)
    n = make_notifier(SimpleNamespace(), 1, "creds.json", SimpleNamespace(fcm_tokens=lambda uid: {7: ["a", "b"]}[uid]), 7, False)
    assert await n.push("hello") == 2
    assert seen == {"project": "proj", "access": "AT", "tokens": ["a", "b"], "text": "hello"}


async def test_friend_notice_is_push_only():
    bot = SimpleNamespace(send_message=AsyncMock())
    n = make_notifier(bot, 42, "", SimpleNamespace(fcm_tokens=lambda uid: []), 7, False)
    await n.telegram("hi", undo_event_id="abc")
    await n.both("brief")
    bot.send_message.assert_not_called()


@pytest.mark.parametrize("owner", [False, True])
@pytest.mark.parametrize("kind", ["conflict", "cap", "added"])
async def test_alert_and_mail_notices_reach_only_users_phones_and_preserve_owner_undo(monkeypatch, owner, kind):
    monkeypatch.setattr("jarvis.proactive.notify.fcm_access_token", lambda path: ("proj", "access"))
    requests = []

    def fcm(request):
        requests.append(json.loads(request.content)["message"])
        return httpx.Response(200, json={"name": "sent"})

    client = httpx.Client
    monkeypatch.setattr("jarvis.proactive.notify.httpx.Client",
                        lambda **kwargs: client(transport=httpx.MockTransport(fcm), **kwargs))
    bot = SimpleNamespace(send_message=AsyncMock())
    devices = SimpleNamespace(fcm_tokens=lambda uid: {1: ["owner-phone"], 2: ["friend-phone"]}[uid])
    notifier = make_notifier(bot, 42, "creds.json", devices, 1 if owner else 2, owner)
    tz = "Europe/Berlin"
    now = datetime(2026, 10, 6, 8, tzinfo=ZoneInfo(tz))
    store = FakeProactiveStore()
    if kind == "conflict":
        def event(uid, start, end):
            return {"id": uid, "summary": uid, "start": f"2026-10-06T{start}:00+02:00",
                    "end": f"2026-10-06T{end}:00+02:00", "all_day": False, "declined": False,
                    "busy": True, "location": None}
        calendar = SimpleNamespace(list_for_proactive=lambda *args: [event("a", "09:00", "10:00"),
                                                                     event("b", "09:30", "11:00")])
        await sweep(calendar, store, notifier, tz, 30, now)
    else:
        store.set_state("mail_cursor", "1000")
        gmail = SimpleNamespace(search_emails=lambda *args: [{"id": "m1", "subject": "Flight booking"}],
                                read_email=lambda mid: {"subject": "Flight booking", "body": "Flight"})
        calendar = MagicMock(list_for_proactive=MagicMock(return_value=[]), create_auto_event=MagicMock(return_value={"id": "e"}))
        extraction = {"found": True, "kind": "flight", "title": "Flight to Berlin", "start": "2026-10-09T08:10:00"}
        llm = FakeProvider({"fast": [AIMessage(json.dumps(extraction))]})
        await MailWatch(gmail, calendar, store, notifier, llm, MemoryAudit(), tz, 0 if kind == "cap" else 5).run_once(now)
    assert len(requests) == 1
    assert requests[0]["token"] == ("owner-phone" if owner else "friend-phone")
    assert requests[0]["data"]["speak"].startswith({"conflict": "Calendar conflict:", "cap": "Auto-add limit", "added": "Added to your calendar:"}[kind])
    if owner:
        args, kwargs = bot.send_message.call_args
        assert args[0] == 42
        if kind == "added":
            assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == f"undo:{event_id_for('m1')}"
        else:
            assert kwargs["reply_markup"] is None
    else:
        bot.send_message.assert_not_called()


async def test_delivery_failure_logs_type_only_and_still_pushes(caplog):
    secret = "sk-notifier-sentinel"
    push = AsyncMock(return_value=1)
    await Notifier(AsyncMock(side_effect=RuntimeError(secret)), push).both("notice", undo_event_id="abc")
    push.assert_awaited_once_with("notice")
    assert "RuntimeError" in caplog.text and secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_fcm_failure_never_logs_response_body_or_tokens(caplog):
    secret = "fcm-token-sentinel"
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403, text=secret))) as client:
        assert send_push("project", "access", [secret], "notice", client) == 0
    assert secret not in caplog.text and "403" in caplog.text
