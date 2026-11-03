"""Embeddings for meaning-based recall, from the user's own OpenAI-compatible endpoint (`POST {base_url}/embeddings`).

Never called while answering a message: only the backfill job (which embeds what changed) and the recall tool (one
query). When the endpoint has no embeddings, or the model is unset, recall falls back to keyword search."""
import hashlib
import logging
import math

import httpx

log = logging.getLogger(__name__)
BATCH = 32
MAX_CHARS = 2000  # per text: a long note is embedded by its start


class Embedder:
    def __init__(self, base_url: str, api_key: str, model: str, http: httpx.AsyncClient):
        self.base_url, self.model, self.http = base_url.rstrip("/"), model, http
        self._key = api_key
        self.available = True  # False after the endpoint refused (no such route or model): no retry until rebuilt

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Unit-length vectors in input order; None when embeddings are unavailable. Other failures raise."""
        if not self.available or not texts:
            return None if not self.available else []
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            chunk = [t[:MAX_CHARS] for t in texts[i:i + BATCH]]
            r = await self.http.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": chunk},
                                     headers={"Authorization": f"Bearer {self._key}"})
            if r.status_code in (400, 404, 405, 422, 501):  # this endpoint or model has no embeddings
                log.warning("embeddings unavailable (HTTP %s): falling back to keyword search", r.status_code)
                self.available = False
                return None
            if r.status_code >= 400:
                raise EmbeddingError(r.status_code)
            try:
                data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
                vecs = [unit([float(x) for x in d["embedding"]]) for d in data]
            except (ValueError, KeyError, TypeError):
                raise EmbeddingError(None) from None
            if len(vecs) != len(chunk):
                raise EmbeddingError(None)
            out += vecs
        return out

    async def aclose(self) -> None:
        await self.http.aclose()


class EmbeddingError(RuntimeError):
    """Only the HTTP status leaves this module: never a body, which may echo the key or the text."""

    def __init__(self, status: int | None):
        super().__init__(f"embedding failed (HTTP {status})" if status else "embedding failed")
        self.status = status


def unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def dot(a: list[float], b: list[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b))


def digest(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\0{text[:MAX_CHARS]}".encode()).hexdigest()
