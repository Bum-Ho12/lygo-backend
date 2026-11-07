import math
from pathlib import Path

import httpx
import pytest

from app.engine.rag.chunking.base import MAX_CHARS, split_text
from app.engine.rag.chunking.sections import chunk_sections
from app.engine.rag.chunking.statute import chunk_statute
from app.engine.rag.embeddings.base import EmbeddingError, HashingEmbedder
from app.engine.rag.embeddings.openai_compat import OpenAICompatEmbedder
from app.engine.rag.retrieval.fusion import rrf
from app.ingestion.structure import parse_structure

FIX = Path(__file__).parents[1] / "fixtures"
pytestmark = pytest.mark.asyncio


# --- chunking ----------------------------------------------------------------------------------------
def test_statute_chunking_keeps_part_section_and_number() -> None:
    chunks = chunk_statute((FIX / "corpus" / "synthetic_act_v1.txt").read_text())
    refs = [c.section_ref for c in chunks]
    assert refs == ["s.1", "s.2", "s.5", "s.6", "s.7"]
    s5 = chunks[2]
    assert s5.heading == "Notice of claim" and "[Part II - CLAIMS]" in s5.text and "thirty days" in s5.text and "(2) An insurer" in s5.text
    assert "Section 5. Notice of claim" in s5.text


def test_statute_without_structure_yields_nothing() -> None:
    assert chunk_statute("just some prose without any numbered sections") == []


def test_long_section_is_split_with_overlap_and_distinct_refs() -> None:
    body = " ".join(f"Sentence number {i} of the very long provision." for i in range(400))
    chunks = chunk_statute(f"PART I - X\n\n9. Long section.\n{body}\n")
    assert len(chunks) > 1 and chunks[0].section_ref == "s.9" and chunks[1].section_ref == "s.9 (part 2)"
    assert all(len(c.text) <= MAX_CHARS + 400 for c in chunks)
    tail = chunks[0].text[-60:].split(".")[0].strip()
    assert tail and tail in chunks[1].text  # overlap carries context across the split


def test_split_text_edge_cases() -> None:
    assert split_text("") == [] and split_text("short") == ["short"]
    huge = "x" * 10_000  # no sentence boundaries at all
    parts = split_text(huge)
    assert len(parts) >= 3 and all(len(p) <= MAX_CHARS for p in parts)


def test_contract_chunks_are_per_clause() -> None:
    doc = parse_structure((FIX / "contract_v1.txt").read_text())
    chunks = chunk_sections(doc.sections)
    assert any(c.section_ref == "clause 3" and "60 days" in c.text for c in chunks) and len(chunks) == len(doc.sections)


# --- fusion ---------------------------------------------------------------------------------------------
def test_rrf_rewards_agreement_and_is_deterministic() -> None:
    fused = rrf([["a", "b", "c"], ["c", "a", "d"]])
    ids = [i for i, _ in fused]
    assert ids[0] == "a" and set(ids) == {"a", "b", "c", "d"}
    assert fused == rrf([["a", "b", "c"], ["c", "a", "d"]])
    assert rrf([]) == [] and [i for i, _ in rrf([["x"], []])] == ["x"]
    scores = dict(fused)
    assert scores["a"] > scores["b"] and math.isclose(scores["a"], 1 / 61 + 1 / 62)


# --- embedders -------------------------------------------------------------------------------------------
async def test_hashing_embedder_properties() -> None:
    e = HashingEmbedder(256)
    v1, v2, v3 = await e.embed(["insurer must pay the claim", "the insurer must pay a claim", "banana smoothie recipe"])
    assert len(v1) == 256 and math.isclose(sum(x * x for x in v1), 1.0, rel_tol=1e-9)
    cos = lambda a, b: sum(x * y for x, y in zip(a, b, strict=True))
    assert cos(v1, v2) > cos(v1, v3) + 0.3
    assert await e.embed(["same text"]) == await e.embed(["same text"])  # deterministic across calls


def _openai(handler, dim=4, **kw) -> OpenAICompatEmbedder:  # type: ignore[no-untyped-def]
    async def nosleep(_: float) -> None: ...

    client = httpx.AsyncClient(base_url="https://llm.test/v1", transport=httpx.MockTransport(handler))
    return OpenAICompatEmbedder("https://llm.test/v1", "sk-test", "embed-x", dim, client=client, sleep=nosleep, **kw)


async def test_openai_embedder_batches_orders_and_authenticates() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.read())
        calls.append((req.headers["authorization"], body["model"], len(body["input"])))
        data = [{"index": i, "embedding": [float(len(t)), 0, 0, 1]} for i, t in enumerate(body["input"])]
        return httpx.Response(200, json={"data": list(reversed(data))})  # out of order on purpose

    e = _openai(handler, batch_size=2)
    vecs = await e.embed(["a", "bb", "ccc", "dddd", "eeeee"])
    assert [v[0] for v in vecs] == [1.0, 2.0, 3.0, 4.0, 5.0]  # restored to input order
    assert calls == [("Bearer sk-test", "embed-x", 2), ("Bearer sk-test", "embed-x", 2), ("Bearer sk-test", "embed-x", 1)]


async def test_openai_embedder_failures() -> None:
    attempts = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0, 0, 0]}]})

    assert await _openai(flaky).embed(["x"]) == [[1.0, 0.0, 0.0, 0.0]] and attempts["n"] == 3
    with pytest.raises(EmbeddingError, match="3 attempts"):
        await _openai(lambda r: httpx.Response(429)).embed(["x"])
    seen = {"n": 0}

    def rejected(req: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        return httpx.Response(400)

    with pytest.raises(EmbeddingError, match="rejected"):
        await _openai(rejected).embed(["x"])
    assert seen["n"] == 1  # 4xx (other than 429) is not retried
    with pytest.raises(EmbeddingError, match="mismatch"):  # wrong dimension must never reach the database
        await _openai(lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 2]}]})).embed(["x"])
    with pytest.raises(EmbeddingError, match="malformed"):
        await _openai(lambda r: httpx.Response(200, json={"oops": 1})).embed(["x"])
