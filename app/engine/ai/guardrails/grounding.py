"""Abstain when retrieval is weak (docs/04 guardrail 6)."""
import re

from app.core.config import settings
from app.engine.rag.retrieval.hybrid import Hit, keywords

INSUFFICIENT = "INSUFFICIENT_SOURCES"
ABSTAIN_MESSAGE = "I couldn't find anything in the available sources that answers this, so I won't guess."
_STOP = frozenset(
    ["the", "and", "for", "are", "was", "were", "with", "that", "this", "from", "have", "has", "had", "not", "but", "you", "your", "can", "will", "shall", "may", "must", "what", "when", "where", "which", "who", "how", "why", "does", "did", "doing", "any", "all", "its", "their", "them", "they", "there", "than", "then", "also", "into", "about", "under", "over", "within", "without", "per", "our", "out", "off"]
)
MIN_COVERAGE = 0.4


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 4 and w.endswith("s") else w


def coverage(question: str, text: str) -> float:
    """Share of the question's meaningful words that appear in the passage (light plural folding)."""
    terms = {_stem(k) for k in keywords(question) if k not in _STOP}
    if not terms:
        return 0.0
    words = {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower())}
    return len(terms & words) / len(terms)


def is_grounded(hits: list[Hit], question: str = "", min_similarity: float | None = None) -> bool:
    """Grounded if a passage covers enough of the question's terms, or is a close enough semantic match."""
    floor = settings.rag_min_similarity if min_similarity is None else min_similarity
    return any(
        (question and coverage(question, h.text) >= MIN_COVERAGE) or (h.vector_score is not None and h.vector_score >= floor) for h in hits
    )
