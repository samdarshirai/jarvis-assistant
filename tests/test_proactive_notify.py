from types import SimpleNamespace
from unittest.mock import AsyncMock

from jarvis.proactive.notify import Notifier, make_notifier


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
    n = make_notifier(bot, 42, "", SimpleNamespace(fcm_tokens=lambda: ["t"]))
    await n.telegram("hi", undo_event_id="abc")
    args, kwargs = bot.send_message.call_args
    assert args == (42, "hi") and kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "undo:abc"


async def test_push_is_a_noop_without_credentials_or_devices():
    assert await make_notifier(SimpleNamespace(), 1, "", SimpleNamespace(fcm_tokens=lambda: ["t"])).push("x") == 0
    assert await make_notifier(SimpleNamespace(), 1, "creds.json", SimpleNamespace(fcm_tokens=lambda: [])).push("x") == 0


async def test_push_sends_to_every_device_token(monkeypatch):
    seen = {}
    monkeypatch.setattr("jarvis.proactive.notify.fcm_access_token", lambda path: ("proj", "AT"))

    def fake_send(project, access, tokens, text, client):
        seen.update(project=project, access=access, tokens=tokens, text=text)
        return len(tokens)

    monkeypatch.setattr("jarvis.proactive.notify.send_push", fake_send)
    n = make_notifier(SimpleNamespace(), 1, "creds.json", SimpleNamespace(fcm_tokens=lambda: ["a", "b"]))
    assert await n.push("hello") == 2
    assert seen == {"project": "proj", "access": "AT", "tokens": ["a", "b"], "text": "hello"}
