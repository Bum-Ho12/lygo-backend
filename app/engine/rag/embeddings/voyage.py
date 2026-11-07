"""Voyage AI embeddings (https://docs.voyageai.com/docs/embeddings).

`voyage-law-2` is the legal-retrieval model (1024 dims only, 16K-token context), matching the `vector(1024)` column.
Passages are embedded with input_type="document" and search queries with input_type="query" (asymmetric retrieval).
Limits per request: 1000 inputs and a model-specific token cap (120K for voyage-law-2), so we batch by both count and an
estimated token budget. HTTP 429 and 5xx are retried with exponential backoff and jitter.
"""
import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx

from app.engine.rag.embeddings.base import EmbeddingError

BASE_URL = "https://api.voyageai.com/v1"
# Conservative per-request budgets (below the documented caps; tokens are estimated at ~4 characters).
_MAX_ITEMS = 128
_TOKEN_BUDGET = 60_000
_CHARS_PER_TOKEN = 4
_RETRY = {429, 500, 502, 503, 504}


def _est_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


class VoyageEmbedder:
    def __init__(
        self, api_key: str, model: str = "voyage-law-2", dim: int = 1024, *, base_url: str = BASE_URL, client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, max_attempts: int = 6, max_context_tokens: int = 16_000,
    ) -> None:
        if not api_key:
            raise EmbeddingError("VOYAGE_API_KEY is not set")
        self.model, self.dim, self._sleep, self._attempts, self._ctx = model, dim, sleep, max_attempts, max_context_tokens
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=60.0)
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self.total_tokens = 0  # cumulative usage reported by the API (cost tracking)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._embed_all(texts, "document")

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed_all([text], "query"))[0]

    def _batches(self, texts: list[str]) -> list[list[int]]:
        batches: list[list[int]] = []
        cur: list[int] = []
        tokens = 0
        for i, t in enumerate(texts):
            n = min(_est_tokens(t), self._ctx)
            if cur and (len(cur) >= _MAX_ITEMS or tokens + n > _TOKEN_BUDGET):
                batches.append(cur)
                cur, tokens = [], 0
            cur.append(i)
            tokens += n
        if cur:
            batches.append(cur)
        return batches

    async def _embed_all(self, texts: list[str], input_type: str) -> list[list[float]]:
        out: list[list[float] | None] = [None] * len(texts)
        for idx in self._batches(texts):
            vecs = await self._call([texts[i] for i in idx], input_type)
            for i, v in zip(idx, vecs, strict=True):
                out[i] = v
        return [v for v in out if v is not None]

    async def _call(self, batch: list[str], input_type: str) -> list[list[float]]:
        payload: dict = {"input": batch, "model": self.model, "input_type": input_type, "truncation": True}  # type: ignore[type-arg]
        last: Exception | None = None
        for attempt in range(self._attempts):
            try:
                r = await self._client.post("/embeddings", json=payload, headers=self._headers)
            except httpx.HTTPError as exc:
                last = exc
            else:
                if r.status_code == 200:
                    return self._parse(r.json(), len(batch))
                detail = self._detail(r)
                if r.status_code in (401, 403):
                    raise EmbeddingError(f"Voyage rejected the API key ({r.status_code})")
                if r.status_code not in _RETRY:
                    raise EmbeddingError(f"Voyage rejected the request ({r.status_code}): {detail}")
                last = EmbeddingError(f"Voyage error {r.status_code}: {detail}")
            await self._sleep(min(60.0, 2**attempt) * (0.5 + random.random() / 2))
        raise EmbeddingError(f"Voyage embedding failed after {self._attempts} attempts: {last}")

    @staticmethod
    def _detail(r: httpx.Response) -> str:
        try:
            return str(r.json().get("detail", ""))[:200]
        except ValueError:
            return ""

    def _parse(self, body: dict, n: int) -> list[list[float]]:  # type: ignore[type-arg]
        try:
            rows = sorted(body["data"], key=lambda d: d["index"])
            vecs = [[float(x) for x in d["embedding"]] for d in rows]
            self.total_tokens += int((body.get("usage") or {}).get("total_tokens", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("malformed Voyage response") from exc
        if len(vecs) != n or any(len(v) != self.dim for v in vecs):
            raise EmbeddingError(f"Voyage returned an unexpected shape (expected {n} x {self.dim}); check VOYAGE_MODEL / EMBEDDING_DIM")
        return vecs
