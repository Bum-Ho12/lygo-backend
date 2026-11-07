"""OpenAI-compatible `/embeddings` client (RumptyCloud inference, OpenAI, vLLM, ...)."""
import asyncio
from collections.abc import Awaitable, Callable

import httpx

from app.engine.rag.embeddings.base import EmbeddingError


class OpenAICompatEmbedder:
    def __init__(
        self, base_url: str, api_key: str, model: str, dim: int, *, batch_size: int = 64,
        client: httpx.AsyncClient | None = None, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, max_attempts: int = 3,
    ) -> None:
        self.model, self.dim, self.batch_size, self._sleep, self._attempts = model, dim, batch_size, sleep, max_attempts
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60.0)
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(await self._batch(texts[i : i + self.batch_size]))
        return out

    async def _batch(self, batch: list[str]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(self._attempts):
            try:
                r = await self._client.post("/embeddings", json={"model": self.model, "input": batch}, headers=self._headers)
            except httpx.HTTPError as exc:
                last = exc
            else:
                if r.status_code == 200:
                    return self._parse(r.json(), len(batch))
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise EmbeddingError(f"embedding request rejected ({r.status_code})")
                last = EmbeddingError(f"embedding service error ({r.status_code})")
            await self._sleep(2**attempt)
        raise EmbeddingError(f"embedding failed after {self._attempts} attempts: {last}")

    def _parse(self, body: dict, n: int) -> list[list[float]]:  # type: ignore[type-arg]
        try:
            rows = sorted(body["data"], key=lambda d: d["index"])
            vecs = [[float(x) for x in d["embedding"]] for d in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("malformed embedding response") from exc
        if len(vecs) != n or any(len(v) != self.dim for v in vecs):
            raise EmbeddingError(f"embedding dimension/count mismatch (expected {n}x{self.dim})")
        return vecs
