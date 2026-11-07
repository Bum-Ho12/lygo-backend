"""Embedding provider interface (ADR 0002). One model for all scopes so vectors are comparable."""
import hashlib
import itertools
import math
import re
from typing import Protocol

from app.core.config import settings


class EmbeddingError(Exception): ...


class Embedder(Protocol):
    model: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Deterministic, dependency-free embedder (signed feature hashing of unigrams + bigrams, L2-normalised).

    NOT semantic: it captures lexical overlap only. It exists so retrieval, isolation and indexing can be tested and
    run locally without a model. Production uses an OpenAI-compatible embedding endpoint.
    """

    model = "hashing-v1"

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim

    def _vec(self, text: str) -> list[float]:
        words = _WORD.findall(text.lower())
        feats = words + [f"{a}_{b}" for a, b in itertools.pairwise(words)]
        v = [0.0] * self.dim
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]
