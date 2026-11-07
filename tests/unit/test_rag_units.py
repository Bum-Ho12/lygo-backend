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


# --- Voyage ----------------------------------------------------------------------------------------------------------------
def _voyage(handler, dim=4, **kw):  # type: ignore[no-untyped-def]
    from app.engine.rag.embeddings.voyage import VoyageEmbedder

    sleeps: list[float] = []

    async def nosleep(s: float) -> None:
        sleeps.append(s)

    client = httpx.AsyncClient(base_url="https://api.voyageai.com/v1", transport=httpx.MockTransport(handler))
    e = VoyageEmbedder("pa-test-key", "voyage-law-2", dim, client=client, sleep=nosleep, **kw)
    e.sleeps = sleeps  # type: ignore[attr-defined]
    return e


def _voyage_ok(req: httpx.Request) -> httpx.Response:
    import json

    body = json.loads(req.read())
    data = [{"object": "embedding", "index": i, "embedding": [float(len(t)), 0.0, 0.0, 1.0]} for i, t in enumerate(body["input"])]
    return httpx.Response(200, json={"object": "list", "data": list(reversed(data)), "model": body["model"], "usage": {"total_tokens": 7 * len(data)}})


async def test_voyage_uses_document_and_query_input_types_and_auth() -> None:
    import json

    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((str(req.url), req.headers["authorization"], json.loads(req.read())))
        return _voyage_ok(req)

    e = _voyage(handler)
    docs = await e.embed(["alpha", "bb"])
    q = await e.embed_query("what is notice?")
    assert [v[0] for v in docs] == [5.0, 2.0] and q[0] == 15.0  # out-of-order response restored to input order
    assert seen[0][0] == "https://api.voyageai.com/v1/embeddings" and seen[0][1] == "Bearer pa-test-key"
    assert (seen[0][2]["input_type"], seen[0][2]["model"], seen[0][2]["truncation"]) == ("document", "voyage-law-2", True)
    assert seen[1][2]["input_type"] == "query" and seen[1][2]["input"] == ["what is notice?"]
    assert e.total_tokens == 21  # usage is tracked for cost visibility


async def test_voyage_batches_by_item_count_and_token_budget() -> None:
    sizes = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        sizes.append(len(json.loads(req.read())["input"]))
        return _voyage_ok(req)

    e = _voyage(handler)
    assert len(await e.embed([f"t{i}" for i in range(300)])) == 300 and sizes == [128, 128, 44]  # <= 128 items per call
    sizes.clear()
    big = "x" * 120_000  # ~30K tokens, but the API truncates at the 16K context, so each counts as 16K => three per 60K batch
    out = await e.embed([big] * 5)
    assert len(out) == 5 and sizes == [3, 2]
    assert [len(b) for b in e._batches([])] == []


async def test_voyage_retries_rate_limits_with_backoff_then_gives_up() -> None:
    n = {"c": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        n["c"] += 1
        return httpx.Response(429, json={"detail": "rate limit"}) if n["c"] < 3 else _voyage_ok(req)

    e = _voyage(flaky)
    assert (await e.embed(["x"]))[0][0] == 1.0 and n["c"] == 3 and len(e.sleeps) == 2 and e.sleeps[1] >= e.sleeps[0] / 2
    always = _voyage(lambda r: httpx.Response(429, json={"detail": "slow down"}), max_attempts=3)
    with pytest.raises(EmbeddingError, match="3 attempts"):
        await always.embed(["x"])


async def test_voyage_error_handling() -> None:
    calls = {"c": 0}

    def bad_key(req: httpx.Request) -> httpx.Response:
        calls["c"] += 1
        return httpx.Response(401, json={"detail": "Provided API key is invalid."})

    with pytest.raises(EmbeddingError, match="API key") as info:
        await _voyage(bad_key).embed(["x"])
    assert calls["c"] == 1 and "pa-test-key" not in str(info.value)  # not retried, key never echoed
    with pytest.raises(EmbeddingError, match="rejected the request"):
        await _voyage(lambda r: httpx.Response(400, json={"detail": "input too long"})).embed(["x"])
    with pytest.raises(EmbeddingError, match="unexpected shape"):  # e.g. configured for 1024 but the model returns 512
        await _voyage(_voyage_ok, dim=1024).embed(["x"])
    with pytest.raises(EmbeddingError, match="malformed"):
        await _voyage(lambda r: httpx.Response(200, json={"oops": 1})).embed(["x"])
    with pytest.raises(EmbeddingError, match="not set"):
        from app.engine.rag.embeddings.voyage import VoyageEmbedder

        VoyageEmbedder("")


def test_factory_prefers_voyage(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.engine.rag.embeddings.factory import get_embedder
    from app.engine.rag.embeddings.voyage import VoyageEmbedder

    monkeypatch.setattr(settings, "voyage_api_key", "pa-x")
    monkeypatch.setattr(settings, "embedding_model", "other")
    monkeypatch.setattr(settings, "llm_base_url", "https://llm.example")
    e = get_embedder()
    assert isinstance(e, VoyageEmbedder) and (e.model, e.dim) == ("voyage-law-2", 1024)
