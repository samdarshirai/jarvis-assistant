import socket

import httpx
import pytest

import jarvis.llm_check as llm_check  # module import: pytest would otherwise collect test_models
from jarvis.llm import LLMCreds
from jarvis.llm_check import LLMCheckError

KEY = "sk-or-v1-supersecretkey"


def creds(base_url="https://api.example.com/v1"):
    return LLMCreds(base_url=base_url, api_key=KEY, fast=["good/fast"], strong=["bad/strong"])


def public(host, port, **kw):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def failing(status, body=""):
    """Transport: `good/*` models answer, every other model gets `status` with `body`."""
    def handler(req):
        assert req.headers["authorization"] == f"Bearer {KEY}"
        if b'"good/' in req.content:
            return httpx.Response(200, json={"choices": [{"message": {"content": "o"}}]})
        return httpx.Response(status, text=body)
    return httpx.MockTransport(handler)


async def check(transport, c=None, resolve=public):
    return await llm_check.test_models(c or creds(), resolve=resolve, transport=transport)


@pytest.mark.asyncio
async def test_test_models_names_failing_model():
    for status, body, code in ((401, "", "rejected_key"), (403, "", "rejected_key"), (402, "", "out_of_credit"),
                               (404, "", "model_not_found"), (400, '{"error": "model bad/strong is not a valid model ID"}',
                                                              "model_not_found")):
        with pytest.raises(LLMCheckError) as e:
            await check(failing(status, body))
        assert (e.value.code, e.value.model) == (code, "bad/strong")
    assert await check(failing(200)) is None


@pytest.mark.asyncio
async def test_test_models_unreachable_and_bad_url():
    def down(req):
        raise httpx.ConnectError("refused")
    with pytest.raises(LLMCheckError) as e:
        await check(httpx.MockTransport(down))
    assert (e.value.code, e.value.model) == ("unreachable", "good/fast")
    with pytest.raises(LLMCheckError) as e:
        await check(failing(200), resolve=lambda h, p, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", p))])
    assert e.value.code == "bad_url"
    with pytest.raises(LLMCheckError) as e:
        await check(failing(200), creds("http://api.example.com/v1"))
    assert e.value.code == "bad_url"


@pytest.mark.asyncio
async def test_key_never_in_error_message():
    def echo(req):
        raise httpx.ConnectError(f"boom {req.headers['authorization']}")
    for transport in (failing(401, f"invalid key {KEY}"), failing(500, f"oops {KEY}"), httpx.MockTransport(echo)):
        with pytest.raises(LLMCheckError) as e:
            await check(transport)
        assert KEY not in str(e.value) and KEY not in repr(e.value) and KEY not in str(e.value.args)


@pytest.mark.asyncio
async def test_a_dripping_endpoint_cannot_hang_the_check(monkeypatch):
    import asyncio

    async def hang(req):
        await asyncio.sleep(3600)  # per-read timeouts never fire on a body that drips
    monkeypatch.setattr(llm_check, "MODEL_DEADLINE", 0.05)
    with pytest.raises(LLMCheckError) as e:
        await check(httpx.MockTransport(hang))
    assert (e.value.code, e.value.model) == ("unreachable", "good/fast")
