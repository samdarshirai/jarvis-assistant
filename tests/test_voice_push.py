import json

import httpx

from jarvis.voice.push import send_push


def test_posts_one_message_per_token_with_text_in_data_and_counts_successes():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = req.read().decode()
        seen.append((str(req.url), req.headers["authorization"], body))
        return httpx.Response(200 if "tok-ok" in body else 404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    n = send_push("proj-1", "AT", ["tok-ok", "tok-gone"], "Good morning. Brief here.", client)
    assert n == 1 and len(seen) == 2
    url, auth, body = seen[0]
    assert url == "https://fcm.googleapis.com/v1/projects/proj-1/messages:send" and auth == "Bearer AT"
    msg = json.loads(body)["message"]  # httpx emits compact JSON, so compare parsed
    assert msg["data"] == {"speak": "Good morning. Brief here."} and msg["android"]["priority"] == "high"


def test_no_tokens_sends_nothing():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("no call"))))
    assert send_push("p", "AT", [], "x", client) == 0


def test_cli_refuses_text_the_speak_frame_would_reject(monkeypatch):
    import pytest

    from jarvis.voice import push
    from jarvis.voice.protocol import MAX_SPEAK_CHARS

    monkeypatch.setattr("sys.argv", ["push", "x" * (MAX_SPEAK_CHARS + 1)])
    with pytest.raises(SystemExit, match="limit is 2000"):
        push.main()
