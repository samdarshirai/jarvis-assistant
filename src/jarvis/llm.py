import asyncio
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

from jarvis.web import FetchError, _check_url, _pin

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
TIMEOUT = 60.0


@dataclass(frozen=True)
class LLMCreds:
    base_url: str
    api_key: str = field(repr=False)
    fast: list[str]
    strong: list[str]


def normalize_base_url(raw: str) -> str:
    """An explicit https:// URL with no trailing slash. Messages never echo the input (a pasted key may be in it)."""
    url = raw.strip().rstrip("/")
    try:
        parts = urlsplit(url)
        parts.port
    except ValueError:
        raise ValueError("That endpoint URL isn't valid.") from None
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("The endpoint must be an https:// URL.")
    if "@" in parts.netloc or parts.query or parts.fragment:
        raise ValueError("The endpoint URL can't carry credentials, a query or a fragment.")
    return url


def creds_for(user_secrets: dict[str, str], s) -> "LLMCreds | None":
    """The user's LLM credentials; None until they set a key. Unset model ids fall back to the server defaults."""
    key = user_secrets.get("llm_key")
    if not key:
        return None

    def ids(name: str, tier: str) -> list[str]:
        return [m.strip() for m in user_secrets.get(name, "").split(",") if m.strip()] or s.models(tier)

    return LLMCreds((user_secrets.get("llm_base_url") or OPENROUTER_BASE).strip().rstrip("/"), key,
                    ids("model_fast", "fast"), ids("model_strong", "strong"))


class BlockedURL(httpx.TransportError):
    pass


class _GuardedTransport(httpx.AsyncBaseTransport):
    """Every request: https only, resolve and refuse non-global addresses, connect to the checked IP (same
    check and pinning as fetch_page), and refuse redirects so a public host can't bounce us inward."""

    def __init__(self, inner: httpx.AsyncBaseTransport, resolve):
        self.inner, self.resolve = inner, resolve

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https":
            raise BlockedURL("Only https endpoints are allowed.", request=request)
        try:
            parts, ip = await asyncio.to_thread(_check_url, str(request.url), self.resolve)
        except FetchError as e:
            raise BlockedURL(str(e), request=request) from None
        pinned, hostport, ext = _pin(parts, ip)
        request.url = httpx.URL(pinned)
        request.headers["Host"] = hostport
        request.extensions = {**request.extensions, **ext}
        response = await self.inner.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise BlockedURL("The endpoint redirected; redirects are refused.", request=request)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


def guarded_http_client(*, resolve=socket.getaddrinfo, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    # An explicit transport also means env proxies are ignored, so nothing bypasses the pinned connection.
    return httpx.AsyncClient(transport=_GuardedTransport(transport or httpx.AsyncHTTPTransport(), resolve),
                             follow_redirects=False, timeout=TIMEOUT)


class AuditCallback(BaseCallbackHandler):
    def __init__(self, audit, secret: str = ""):
        self.audit = audit
        self.secret = secret  # redacted from error text before it is recorded
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
        text = str(error).replace(self.secret, "***") if self.secret else str(error)
        self.audit.record("llm", "error", result={"error": text}, latency_ms=self._latency(run_id))


class LLMProvider:
    def __init__(self, creds: LLMCreds, audit):
        self.creds = creds
        self.cb = AuditCallback(audit, creds.api_key)
        self.http = guarded_http_client()

    def _models(self, tier: str) -> list[ChatOpenAI]:
        extra = None
        if urlsplit(self.creds.base_url).hostname == "openrouter.ai":  # other endpoints get a plain request
            provider = {"data_collection": "deny", "require_parameters": True}
            if tier == "fast":
                provider["sort"] = "latency"
            extra = {"provider": provider, "usage": {"include": True}}
        # ponytail: only the async path is guarded (every caller uses ainvoke); pass a guarded http_client if sync calls appear.
        return [
            ChatOpenAI(model=m, api_key=self.creds.api_key, base_url=self.creds.base_url, temperature=0,
                       callbacks=[self.cb], extra_body=extra, http_async_client=self.http)
            for m in {"fast": self.creds.fast, "strong": self.creds.strong}[tier]
        ]

    async def aclose(self) -> None:
        await self.http.aclose()

    def get(self, tier: str, tools: list | None = None):
        models = [m.bind_tools(tools) if tools else m for m in self._models(tier)]
        return models[0].with_fallbacks(models[1:]) if len(models) > 1 else models[0]
