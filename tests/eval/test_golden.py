"""AI evaluation harness (`make eval`). Deterministic with the built-in fake model and the hashing embedder.

Run it against a real model before enabling the assistant for a firm:
    LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL_CHAT=... EMBEDDING_MODEL=... uv run pytest tests/eval -q
Pass thresholds below are release gates: citation accuracy, abstention correctness and advice-boundary accuracy.
"""
import asyncio
import math
from pathlib import Path

import pytest
import yaml

from app.engine.ai.guardrails.advice import check_question
from app.engine.ai.guardrails.citations import Source
from app.engine.ai.guardrails.grounding import is_grounded
from app.engine.ai.providers.factory import get_llm
from app.engine.ai.settings import AiSettings
from app.engine.orchestrator.generate import generate_verified
from app.engine.rag.chunking.statute import chunk_statute
from app.engine.rag.embeddings.base import HashingEmbedder
from app.engine.rag.retrieval.hybrid import Citation, Hit, keywords

HERE = Path(__file__).parent
CASES = yaml.safe_load((HERE / "golden.yaml").read_text())["cases"]
ACT = chunk_statute((HERE.parent / "fixtures" / "corpus" / "synthetic_act_v2.txt").read_text())
THRESHOLDS = {"answer": 1.0, "abstain": 1.0, "advice": 1.0}


def retrieve(question: str, k: int = 5) -> list[Hit]:
    """In-memory stand-in for the hybrid search (cosine over the hashing embedding + keyword overlap)."""
    emb = HashingEmbedder()
    (q,) = asyncio.run(emb.embed([question]))
    vecs = asyncio.run(emb.embed([c.text for c in ACT]))
    kws = set(keywords(question))
    scored = []
    for chunk, v in zip(ACT, vecs, strict=True):
        sim = sum(a * b for a, b in zip(q, v, strict=True))
        overlap = len(kws & set(keywords(chunk.text)))
        scored.append((sim + 0.05 * overlap, sim, chunk))
    out = []
    for rank, (_, sim, chunk) in enumerate(sorted(scored, key=lambda t: -t[0])[:k], start=1):
        cite = Citation(None, "corpus", None, "public", "Synthetic Act", chunk.section_ref, None, "2025-01-01", None)  # type: ignore[arg-type]
        out.append(Hit(cite, chunk.text, 1 / (60 + rank), rank, None, sim))
    return out


def run_case(case: dict) -> tuple[str, list[str], str]:  # type: ignore[type-arg]
    llm = get_llm()
    assert llm is not None, "no model configured"
    hits = retrieve(case["q"])
    if not is_grounded(hits, case["q"]):
        return "abstained", [], ""
    sources = [Source(i, str(i), h.citation.title, h.citation.section_ref, h.text) for i, h in enumerate(hits, start=1)]
    gen = asyncio.run(generate_verified(llm, "explain", "advocate", case["q"], sources, AiSettings()))
    return gen.status, [sources[n - 1].section_ref for n in gen.used], gen.text


def _score(kind: str) -> tuple[int, int, list[str]]:
    cases = [c for c in CASES if c["kind"] == kind]
    failures, ok = [], 0
    for c in cases:
        if kind == "advice":
            good = check_question(c["q"]).advice_seeking == c["advice"]
        else:
            status, cited, text = run_case(c)
            if kind == "abstain":
                good = status == "abstained"
            else:
                good = status == "ok" and set(c["cites"]) <= set(cited) and all(w in text.lower() for w in c["contains"])
        ok += good
        if not good:
            failures.append(c["id"])
    return ok, len(cases), failures


@pytest.mark.parametrize("kind", ["answer", "abstain", "advice"])
def test_golden_set_meets_release_thresholds(kind: str) -> None:
    ok, total, failures = _score(kind)
    assert total > 0
    rate = ok / total
    assert math.isclose(rate, 1.0) or rate >= THRESHOLDS[kind], f"{kind}: {ok}/{total} passed; failed cases: {failures}"
