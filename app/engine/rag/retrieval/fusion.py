"""Reciprocal-rank fusion of ranked id lists (vector + full-text)."""
from collections import defaultdict
from collections.abc import Hashable, Sequence


def rrf[T: Hashable](rankings: Sequence[Sequence[T]], k: int = 60) -> list[tuple[T, float]]:
    """score(d) = sum over rankings of 1/(k + rank(d)), rank starting at 1. Ties broken by first appearance."""
    scores: dict[T, float] = defaultdict(float)
    first: dict[T, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] += 1.0 / (k + rank)
            first.setdefault(item, len(first))
    return sorted(scores.items(), key=lambda kv: (-kv[1], first[kv[0]]))
