from uuid import uuid4

from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.messages import AIMessage

from jarvis.config import Settings
from jarvis.llm import AuditCallback, LLMProvider
from tests.fakes import MemoryAudit


def settings():
    return Settings(_env_file=None, openrouter_api_key="k", models_fast="a/x,b/y", models_strong="c/z",
                    database_url="postgresql://x", telegram_bot_token="t", telegram_owner_chat_id=1, fernet_key="f")


def test_openrouter_privacy_params_on_every_model():
    p = LLMProvider(settings(), MemoryAudit())
    for tier, n in (("fast", 2), ("strong", 1)):
        models = p._models(tier)
        assert len(models) == n
        for m in models:
            prov = m.extra_body["provider"]
            assert prov["data_collection"] == "deny" and prov["require_parameters"] is True
            assert str(m.openai_api_base) == "https://openrouter.ai/api/v1"


def test_fast_tier_sorts_by_latency_strong_does_not():
    p = LLMProvider(settings(), MemoryAudit())
    assert p._models("fast")[0].extra_body["provider"]["sort"] == "latency"
    assert "sort" not in p._models("strong")[0].extra_body["provider"]


def test_get_builds_fallback_chain():
    p = LLMProvider(settings(), MemoryAudit())
    assert type(p.get("fast")).__name__ == "RunnableWithFallbacks"


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
    assert audit.records[0]["result"] == {"error": "boom"}
