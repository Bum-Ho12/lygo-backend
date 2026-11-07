"""Phase E1: corpus loading/versioning, indexing in the pipeline, hybrid search and its isolation guarantees."""
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.engine.rag.corpus import CorpusManifest, LicenceNotConfirmed, load_corpus_document
from app.engine.rag.embeddings.base import EmbeddingError
from tests.conftest import PgConfig
from tests.integration.helpers import make_user, owner_sql, token_from
from tests.integration.test_documents_api import files, setup_firm, upload_client

pytestmark = pytest.mark.asyncio
CORPUS = Path(__file__).parents[1] / "fixtures" / "corpus"


def manifest(version: str, **kw) -> CorpusManifest:  # type: ignore[no-untyped-def]
    base = {"id": "synthetic-insurance-test-act", "title": "Synthetic Insurance Test Act", "kind": "act", "version_date": version,
            "licence": "test fixture", "licence_confirmed": True}
    return CorpusManifest(**{**base, **kw})


async def platform_headers(api, database):  # type: ignore[no-untyped-def]
    uid, h, _ = await make_user(api, "platform")
    owner_sql(database, "UPDATE users SET platform_role='platform_admin' WHERE id=%s", uid)
    return h


@pytest.fixture
async def act(factory, embedder, database: PgConfig):  # type: ignore[no-untyped-def]
    """Load the synthetic Act (v1 in force from 2020, replaced by v2 from 2025)."""
    v1 = (CORPUS / "synthetic_act_v1.txt").read_text()
    v2 = (CORPUS / "synthetic_act_v2.txt").read_text()
    # idempotent across tests: this module-scoped data is loaded once per database
    d1, n1 = await load_corpus_document(factory, embedder, manifest("2020-01-01"), v1)
    d2, n2 = await load_corpus_document(factory, embedder, manifest("2025-01-01"), v2)
    return d1, d2, n1, n2


# --- corpus loading & versioning -----------------------------------------------------------------------
async def test_corpus_versions_supersede_and_reload_is_idempotent(act, factory, embedder, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    d1, d2, *_ = act
    assert d1.id != d2.id
    chunks = lambda d: owner_sql(database, "SELECT count(*), min(effective_to), max(effective_from) FROM chunks WHERE source_id = %s", d.id)[0]
    assert chunks(d2)[0] == 5 and chunks(d1)[0] == 5
    assert str(chunks(d1)[1]) == "2025-01-01"  # v1 stopped being law when v2 took effect
    assert chunks(d2)[1] is None and str(chunks(d2)[2]) == "2025-01-01"
    again, n = await load_corpus_document(factory, embedder, manifest("2025-01-01"), (CORPUS / "synthetic_act_v2.txt").read_text())
    assert again.id == d2.id and n == 0  # unchanged checksum => no re-embedding


async def test_as_at_search_returns_the_version_in_force(api, act, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    h = await platform_headers(api, database)
    now = (await api.get("/v1/corpus/search", params={"q": "notice of claim days of the loss"}, headers=h)).json()
    assert now and now[0]["citation"]["section_ref"] == "s.5" and "sixty days" in now[0]["text"] and now[0]["citation"]["version"] == "2025-01-01"
    old = (await api.get("/v1/corpus/search", params={"q": "notice of claim days of the loss", "as_at": "2023-06-01"}, headers=h)).json()
    assert "thirty days" in old[0]["text"] and old[0]["citation"]["version"] == "2020-01-01"
    assert not any("sixty days" in x["text"] for x in old)  # the later version is not yet law at that date
    before_any = (await api.get("/v1/corpus/search", params={"q": "notice of claim", "as_at": "2010-01-01"}, headers=h)).json()
    assert before_any == []


async def test_open_a_citation_and_unauthenticated_access(api, act, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    h = await platform_headers(api, database)
    hit = (await api.get("/v1/corpus/search", params={"q": "insurer shall pay a settled claim within ninety days"}, headers=h)).json()[0]
    assert hit["citation"]["section_ref"] == "s.6"
    sec = await api.get(f"/v1/corpus/documents/{hit['citation']['source_id']}/sections/s.6", headers=h)
    assert sec.status_code == 200 and "ninety days" in sec.json()[0]["text"]
    assert (await api.get(f"/v1/corpus/documents/{hit['citation']['source_id']}/sections/s.99", headers=h)).status_code == 404
    assert (await api.get("/v1/corpus/search", params={"q": "claim"})).status_code == 401


async def test_licence_gate_in_production(factory, embedder, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(LicenceNotConfirmed):
        await load_corpus_document(factory, embedder, manifest("2031-01-01", licence_confirmed=False), "1. Short title.\nText of the thing here.")


async def test_platform_endpoint_loads_corpus_and_enforces_rights(api, database: PgConfig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ph = await platform_headers(api, database)
    _, plain, _ = await make_user(api, "plain")
    body = {"manifest": {"id": f"api-act-{uuid.uuid4().hex[:6]}", "title": "API Act", "kind": "act", "version_date": "2026-01-01", "licence_confirmed": True},
            "text": "PART I - X\n\n1. Short title.\nThis is the API test Act, a fixture only."}
    assert (await api.post("/v1/platform/corpus/documents", json=body, headers=plain)).status_code == 403
    r = await api.post("/v1/platform/corpus/documents", json=body, headers=ph)
    assert r.status_code == 201 and r.json()["chunks"] == 1
    monkeypatch.setattr(settings, "app_env", "production")
    body["manifest"].update(id=f"api-act-{uuid.uuid4().hex[:6]}", licence_confirmed=False)
    assert (await api.post("/v1/platform/corpus/documents", json=body, headers=ph)).status_code == 422


# --- indexing in the pipeline ----------------------------------------------------------------------------
async def test_pipeline_indexes_documents_and_reprocess_replaces_chunks(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    doc = (await upload_client(api, firm, cid, adv)).json()
    d = (await api.get(f"/v1/firms/{firm}/documents/{doc['id']}", headers=adv)).json()
    assert [e["stage"] for e in d["events"]][-1] == "index" and d["events"][-1]["status"] == "ok"
    count = lambda: owner_sql(database, "SELECT count(*), min(scope), bool_and(embed_model = 'hashing-v1') FROM chunks WHERE source_id = %s", doc["id"])[0]
    n, scope, ok = count()
    assert n >= 3 and scope == "client" and ok
    await api.post(f"/v1/firms/{firm}/documents/{doc['id']}:reprocess", headers=adv)
    assert count()[0] == n  # replaced, not duplicated


async def test_embedding_outage_does_not_lose_the_document(api, database: PgConfig, embedder, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)

    async def down(texts):  # type: ignore[no-untyped-def]
        raise EmbeddingError("embedding service unavailable")

    monkeypatch.setattr(embedder, "embed", down)
    doc = (await upload_client(api, firm, cid, adv)).json()
    d = (await api.get(f"/v1/firms/{firm}/documents/{doc['id']}", headers=adv)).json()
    assert d["status"] == "processed" and d["status_detail"].startswith("index_failed")  # parsed + flagged, just not searchable yet
    assert d["events"][-1]["stage"] == "index" and d["events"][-1]["status"] == "error"
    assert len((await api.get(f"/v1/firms/{firm}/documents/{doc['id']}/flags", headers=adv)).json()) >= 2


# --- search + isolation ---------------------------------------------------------------------------------
async def portal(api, outbox, firm, adv, cid):  # type: ignore[no-untyped-def]
    _, h, email = await make_user(api, "portal")
    await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": email}, headers=adv)
    assert (await api.post("/v1/invitations/accept", json={"token": token_from(outbox, email)}, headers=h)).status_code == 200
    return h


async def fsearch(api, firm, headers, q, **kw):  # type: ignore[no-untyped-def]
    r = await api.post(f"/v1/firms/{firm}/search", json={"query": q, **kw}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


SECRET = b"Confidential memorandum about the zanzibar-merger-protocol and the quasar settlement terms."


async def test_firm_search_never_crosses_firms_even_with_an_exact_match(api, database: PgConfig, act) -> None:  # type: ignore[no-untyped-def]
    f1, a1, _ = await setup_firm(api, database)
    f2, a2, _ = await setup_firm(api, database)
    kb = await api.post(f"/v1/firms/{f1}/knowledge/documents", headers=a1, **files("memo.txt", SECRET, title="Memo"))
    assert kb.status_code == 201
    mine = await fsearch(api, f1, a1, "zanzibar-merger-protocol quasar settlement")
    assert mine and mine[0]["citation"]["scope"] == "firm_kb" and "zanzibar" in mine[0]["text"]
    theirs = await fsearch(api, f2, a2, "zanzibar-merger-protocol quasar settlement")
    assert all("zanzibar" not in h["text"] for h in theirs)  # exact phrase, other tenant: nothing
    assert all(h["citation"]["scope"] == "public" for h in theirs)  # only public law may appear
    assert (await api.post(f"/v1/firms/{f1}/search", json={"query": "zanzibar"}, headers=a2)).status_code == 404  # not a member


async def test_raw_unfiltered_chunk_queries_still_cannot_leak(api, database: PgConfig, factory, act) -> None:  # type: ignore[no-untyped-def]
    """The 'forgotten filter' test: even a query with no WHERE clause sees only the caller's rows (docs/03)."""
    from sqlalchemy import text

    from app.core.tenancy import TenantContext, tenant_session

    f1, a1, _ = await setup_firm(api, database)
    f2, _, _ = await setup_firm(api, database)
    await api.post(f"/v1/firms/{f1}/knowledge/documents", headers=a1, **files("m.txt", SECRET))
    ctx = TenantContext(firm_id=f2, user_id=uuid.uuid4(), role="advocate")
    async with tenant_session(factory, ctx) as s:
        rows = (await s.execute(text("SELECT scope, firm_id FROM chunks"))).all()
        assert rows and {r.scope for r in rows} == {"public"} and all(r.firm_id is None for r in rows)


async def test_client_sees_own_chunks_shared_knowledge_and_public_law_only(api, database: PgConfig, outbox, act) -> None:  # type: ignore[no-untyped-def]
    firm, adv, c1 = await setup_firm(api, database)
    c2 = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Sibling"}, headers=adv)).json()["id"]
    await upload_client(api, firm, c1, adv, name="mine.txt", data=b"Policy of Jane: the vulcan roof endorsement applies to her motor insurance policy premium.")
    await upload_client(api, firm, c2, adv, name="theirs.txt", data=b"Policy of Joe: the zeppelin hangar endorsement applies to his insurance policy premium.")
    kb = (await api.post(f"/v1/firms/{firm}/knowledge/documents", headers=adv, **files("guide.txt", b"Firm guide: how a claimant should document a vehicle loss, the walrus checklist."))).json()
    ph = await portal(api, outbox, firm, adv, c1)

    own = await fsearch(api, firm, ph, "vulcan roof endorsement")
    assert own and "vulcan" in own[0]["text"] and own[0]["citation"]["scope"] == "client"
    assert all("zeppelin" not in h["text"] for h in await fsearch(api, firm, ph, "zeppelin hangar endorsement"))  # sibling client's document
    assert all(h["citation"]["scope"] != "firm_kb" for h in await fsearch(api, firm, ph, "walrus checklist"))  # not shared yet
    await api.patch(f"/v1/firms/{firm}/knowledge/documents/{kb['id']}", json={"shared_with_clients": True}, headers=adv)
    shared = await fsearch(api, firm, ph, "walrus checklist")
    assert shared and shared[0]["citation"]["scope"] == "firm_kb"
    await api.patch(f"/v1/firms/{firm}/knowledge/documents/{kb['id']}", json={"shared_with_clients": False}, headers=adv)
    assert all(h["citation"]["scope"] != "firm_kb" for h in await fsearch(api, firm, ph, "walrus checklist"))  # revoked immediately
    law = await fsearch(api, firm, ph, "claimant notice of claim within days", scopes=["public"])
    assert law and all(h["citation"]["scope"] == "public" for h in law)  # clients can read public law
    firm_view = await fsearch(api, firm, adv, "zeppelin hangar endorsement")
    assert firm_view and "zeppelin" in firm_view[0]["text"]  # the firm sees every client of its own


async def test_scope_and_matter_filters_and_validation(api, database: PgConfig, act) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    m1 = (await api.post(f"/v1/firms/{firm}/clients/{cid}/matters", json={"title": "M1"}, headers=adv)).json()["id"]
    await upload_client(api, firm, cid, adv, name="d.txt", data=b"Matter one document about the kestrel indemnity clause for the insured.", matter_id=m1)
    await upload_client(api, firm, cid, adv, name="e.txt", data=b"Unassigned document about the kestrel indemnity clause for another insured.")
    only_public = await fsearch(api, firm, adv, "kestrel indemnity", scopes=["public"])
    assert all(h["citation"]["scope"] == "public" for h in only_public)
    both = await fsearch(api, firm, adv, "kestrel indemnity clause", scopes=["client"])
    assert len([h for h in both if "kestrel" in h["text"]]) == 2
    scoped = await fsearch(api, firm, adv, "kestrel indemnity clause", scopes=["client"], matter_id=m1)
    assert len([h for h in scoped if "kestrel" in h["text"]]) == 1 and "Matter one" in scoped[0]["text"]
    assert (await api.post(f"/v1/firms/{firm}/search", json={"query": "x", "scopes": ["secret"]}, headers=adv)).status_code == 422
    assert (await api.post(f"/v1/firms/{firm}/search", json={"query": ""}, headers=adv)).status_code == 422
    assert (await fsearch(api, firm, adv, "nothing matches xyzzyplugh", scopes=["client"])) is not None


async def test_hybrid_ranks_report_both_signals(api, database: PgConfig, act) -> None:  # type: ignore[no-untyped-def]
    firm, adv, _ = await setup_firm(api, database)
    hits = await fsearch(api, firm, adv, "settled claim within ninety days", scopes=["public"])
    assert hits[0]["citation"]["section_ref"] == "s.6"
    assert hits[0]["vector_rank"] is not None and hits[0]["text_rank"] is not None  # found by both retrievers => fused rank 1
    assert hits[0]["score"] > hits[-1]["score"]


async def test_search_requires_a_configured_model(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    from app.engine.rag.embeddings.factory import get_embedder
    from app.main import app

    firm, adv, _ = await setup_firm(api, database)
    app.dependency_overrides[get_embedder] = lambda: None
    r = await api.post(f"/v1/firms/{firm}/search", json={"query": "claim"}, headers=adv)
    assert r.status_code == 503 and r.json()["code"] == "search_unavailable"


async def test_natural_language_questions_reach_the_full_text_index(api, database: PgConfig, act) -> None:  # type: ignore[no-untyped-def]
    """Regression: with AND semantics a full question never matched, so only the vector leg ever contributed."""
    firm, adv, _ = await setup_firm(api, database)
    hits = await fsearch(api, firm, adv, "When must a claimant give notice of a claim to the insurer within days of the loss?", scopes=["public"])
    top = hits[0]
    assert top["citation"]["section_ref"] == "s.5" and top["text_rank"] == 1 and top["vector_rank"] is not None
