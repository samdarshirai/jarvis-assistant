import asyncio
import socket

import httpx

from jarvis.llm import BlockedURL, LLMCreds, guarded_http_client, normalize_base_url

TIMEOUT = 20.0
MODEL_DEADLINE = 30.0  # whole call: httpx's timeout is per read, so a dripping endpoint would otherwise hang the PUT


class LLMCheckError(Exception):
    """Built from the code and model id only, so the key (or a body echoing it) never reaches the message."""

    def __init__(self, code: str, model: str | None = None):
        super().__init__(code if model is None else f"{code}: {model}")
        self.code, self.model = code, model


def _code(r: httpx.Response) -> str | None:
    if r.is_success:
        return None
    if r.status_code in (401, 403):
        return "rejected_key"
    if r.status_code == 402:
        return "out_of_credit"
    if r.status_code == 404 or "model" in r.text.lower():
        return "model_not_found"
    return "unreachable"


async def test_models(creds: LLMCreds, *, resolve=socket.getaddrinfo, transport=None) -> None:
    """One 1-token completion per model in fast + strong, through the SSRF-guarded client; raises LLMCheckError."""
    try:
        base = normalize_base_url(creds.base_url)
    except ValueError:
        raise LLMCheckError("bad_url") from None
    async with guarded_http_client(resolve=resolve, transport=transport) as client:
        for model in creds.fast + creds.strong:
            try:
                r = await asyncio.wait_for(client.post(
                    f"{base}/chat/completions", timeout=TIMEOUT, headers={"Authorization": f"Bearer {creds.api_key}"},
                    json={"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}),
                    MODEL_DEADLINE)
            except BlockedURL:
                raise LLMCheckError("bad_url", model) from None
            except (httpx.HTTPError, TimeoutError):
                raise LLMCheckError("unreachable", model) from None
            code = _code(r)
            if code:
                raise LLMCheckError(code, model)


test_models.__test__ = False  # not a pytest test, despite the name
