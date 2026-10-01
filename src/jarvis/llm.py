import time

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class AuditCallback(BaseCallbackHandler):
    def __init__(self, audit):
        self.audit = audit
        self._t0: dict = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._t0[run_id] = time.monotonic()

    def _latency(self, run_id):
        t0 = self._t0.pop(run_id, None)
        return None if t0 is None else int((time.monotonic() - t0) * 1000)

    def on_llm_end(self, response, *, run_id, **kwargs):
        out = response.llm_output or {}
        usage = out.get("token_usage") or {}
        model = out.get("model_name", "unknown")
        self.audit.record("llm", model, model=model, latency_ms=self._latency(run_id),
                          tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"),
                          cost_usd=usage.get("cost"))

    def on_llm_error(self, error, *, run_id, **kwargs):
        self.audit.record("llm", "error", result={"error": str(error)}, latency_ms=self._latency(run_id))


class LLMProvider:
    def __init__(self, settings, audit):
        self.s = settings
        self.cb = AuditCallback(audit)

    def _models(self, tier: str) -> list[ChatOpenAI]:
        provider = {"data_collection": "deny", "require_parameters": True}
        if tier == "fast":
            provider["sort"] = "latency"
        return [
            ChatOpenAI(model=m, api_key=self.s.openrouter_api_key, base_url=OPENROUTER_BASE, temperature=0,
                       callbacks=[self.cb], extra_body={"provider": provider, "usage": {"include": True}})
            for m in self.s.models(tier)
        ]

    def get(self, tier: str, tools: list | None = None):
        models = [m.bind_tools(tools) if tools else m for m in self._models(tier)]
        return models[0].with_fallbacks(models[1:]) if len(models) > 1 else models[0]
