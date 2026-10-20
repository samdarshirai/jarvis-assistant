import socket
import traceback
from uuid import uuid4

import httpx
import pytest
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.messages import AIMessage

from jarvis.config import Settings
from jarvis.llm import AuditCallback, BlockedURL, LLMCreds, LLMProvider, creds_for, guarded_http_client, normalize_base_url
from tests.fakes import MemoryAudit

OR = "https://openrouter.ai/api/v1"


def creds(base_url=OR):
    return LLMCreds(base_url=base_url, api_key="k", fast=["a/x", "b/y"], strong=["c/z"])


def settings():
    return Settings(_env_file=None, openrouter_api_key="env", models_fast="a/x,b/y", models_strong="c/z",
                    database_url="postgresql://x", telegram_bot_token="t", telegram_owner_chat_id=1, fernet_key="f")


def resolver(ip):
    return lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


def test_openrouter_extras_only_for_openrouter_host():
    p = LLMProvider(creds(), MemoryAudit())
    for tier, n in (("fast", 2), ("strong", 1)):
        models = p._models(tier)
        assert len(models) == n
        for m in models:
            prov = m.extra_body["provider"]
            assert prov["data_collection"] == "deny" and prov["require_parameters"] is True
            assert m.extra_body["usage"] == {"include": True}
            assert str(m.openai_api_base) == OR
            assert m.http_async_client is not None
    assert p._models("fast")[0].extra_body["provider"]["sort"] == "latency"
    assert "sort" not in p._models("strong")[0].extra_body["provider"]
    # a lookalike host is not openrouter
    assert not LLMProvider(creds("https://openrouter.ai.evil.com/v1"), MemoryAudit())._models("fast")[0].extra_body


def test_other_host_gets_plain_request():
    p = LLMProvider(creds("https://api.example.com/v1"), MemoryAudit())
    m = p._models("fast")[0]
    assert not m.extra_body
    assert str(m.openai_api_base) == "https://api.example.com/v1"
    assert m.http_async_client is not None


def test_get_builds_fallback_chain():
    p = LLMProvider(creds(), MemoryAudit())
    assert type(p.get("fast")).__name__ == "RunnableWithFallbacks"


def test_creds_for_defaults_and_overrides():
    s = settings()
    assert creds_for({}, s) is None
    assert creds_for({"llm_base_url": "https://x.example/v1"}, s) is None
    c = creds_for({"llm_key": "sk"}, s)
    assert c == LLMCreds(OR, "sk", ["a/x", "b/y"], ["c/z"])
    c = creds_for({"llm_key": "sk", "llm_base_url": "https://x.example/v1/", "model_fast": " m1 , m2", "model_strong": "m3"}, s)
    assert c == LLMCreds("https://x.example/v1", "sk", ["m1", "m2"], ["m3"])


def test_normalize_rejects_http_and_no_scheme():
    for bad in ("http://api.example.com/v1", "api.example.com/v1", "ftp://x.example", "https://", "",
                "https://user:pw@x.example/v1", "https://x.example/v1?a=1", "https://x.example/v1#f"):
        with pytest.raises(ValueError):
            normalize_base_url(bad)


def test_normalize_strips_whitespace_and_trailing_slash():
    assert normalize_base_url("  https://api.example.com/v1/ \n") == "https://api.example.com/v1"
    assert normalize_base_url("https://openrouter.ai/api/v1") == OR


async def _get(url, resolve, inner=None):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={})

    async with guarded_http_client(resolve=resolve, transport=inner or httpx.MockTransport(handler)) as c:
        r = await c.get(url)
    return r, seen


@pytest.mark.asyncio
async def test_guard_rejects_loopback_link_local_metadata_and_private():
    for url in ("https://127.0.0.1/v1/models", "https://localhost/v1/models",
                "https://169.254.169.254/latest/meta-data", "https://10.0.0.5/v1/models", "http://8.8.8.8/v1"):
        with pytest.raises(BlockedURL):
            await _get(url, socket.getaddrinfo, httpx.MockTransport(lambda r: pytest.fail("connected")))


@pytest.mark.asyncio
async def test_guard_rejects_hostname_resolving_private():
    with pytest.raises(BlockedURL):
        await _get("https://api.example.com/v1/models", resolver("192.168.1.7"),
                   httpx.MockTransport(lambda r: pytest.fail("connected")))
    # a public answer is pinned: connect to the checked IP, keep the real host for Host and SNI
    r, seen = await _get("https://api.example.com/v1/models", resolver("93.184.216.34"))
    assert r.status_code == 200
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "api.example.com"
    assert seen[0].extensions["sni_hostname"] == "api.example.com"


@pytest.mark.asyncio
async def test_redirect_to_private_refused():
    hits = []

    def handler(req):
        hits.append(req)
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest/meta-data"})

    with pytest.raises(BlockedURL):
        await _get("https://api.example.com/v1/models", resolver("93.184.216.34"), httpx.MockTransport(handler))
    assert len(hits) == 1


def test_callback_records_llm_call():
    audit = MemoryAudit()
    cb = AuditCallback(audit)
    rid = uuid4()
    cb.on_chat_model_start({}, [[]], run_id=rid)
    res = LLMResult(generations=[[ChatGeneration(message=AIMessage("hi"))]],
                    llm_output={"model_name": "a/x", "token_usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.001}})
    cb.on_llm_end(res, run_id=rid)
    r = audit.records[0]
    assert (r["kind"], r["model"], r["tokens_in"], r["tokens_out"], r["cost_usd"]) == ("llm", "a/x", 10, 3, 0.001)
    assert r["latency_ms"] >= 0


def test_callback_records_failure():
    audit = MemoryAudit()
    cb = AuditCallback(audit)
    cb.on_llm_error(RuntimeError("boom"), run_id=uuid4())
    assert audit.records[0]["result"] == {"error": "provider_failed"}


def test_callback_redacts_key_from_errors():
    audit = MemoryAudit()
    AuditCallback(audit, secret="sk-secret-123").on_llm_error(RuntimeError("bad key sk-secret-123"), run_id=uuid4())
    assert "sk-secret-123" not in str(audit.records[0])


@pytest.mark.parametrize("status,code", [(401, "rejected_key"), (403, "rejected_key"), (402, "out_of_credit"),
                                       (429, "rate_limited"), (500, "provider_failed")])
@pytest.mark.parametrize("sync", [False, True])
async def test_provider_error_preserves_status_without_body_request_or_chain(status, code, sync, caplog):
    key = "sk-synthetic-status-body-secret"
    audit = MemoryAudit()
    provider = LLMProvider(LLMCreds("https://provider.example/v1", key, ["m"], ["m"]), audit)

    def reject(request):
        assert request.headers["Authorization"] == f"Bearer {key}"
        return httpx.Response(status, json={"error": {"message": f"Rejected {key}"}})

    await provider.http.aclose()
    provider.http = httpx.AsyncClient(transport=httpx.MockTransport(reject))
    model = provider.get("fast")
    model.root_async_client.max_retries = 0
    with httpx.Client(transport=httpx.MockTransport(reject)) as http:
        model.root_client = model.root_client.with_options(http_client=http, max_retries=0)
        model.client = model.root_client.chat.completions
        try:
            with pytest.raises(Exception) as caught:
                if sync:
                    model.invoke("hello")
                else:
                    await model.ainvoke("hello")
        finally:
            await provider.aclose()
    error = caught.value
    assert getattr(error, "status_code", None) == status
    assert getattr(error, "code", None) == code
    assert error.__cause__ is None and error.__context__ is None
    assert not hasattr(error, "request") and not hasattr(error, "response") and not hasattr(error, "body")
    assert audit.records
    assert key not in "".join(traceback.format_exception(error))
    assert key not in caplog.text and key not in str(audit.records)
