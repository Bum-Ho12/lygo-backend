"""Each external dependency failing must produce a clean, retryable error response, never a 500 (and never a stack trace)."""
import pytest

from app.engine.rag.embeddings.base import EmbeddingError
from app.storage.base import StorageError
from tests.conftest import PgConfig
from tests.integration.helpers import firm_member, make_user, new_firm, owner_sql
from tests.integration.test_documents_api import files

pytestmark = pytest.mark.asyncio


async def _firm(api, database: PgConfig):  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, adv, _ = await firm_member(api, database, firm, "advocate")
    cid = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Acme"}, headers=adv)).json()["id"]
    return firm, adv, cid


async def test_search_outage_is_a_503_not_a_500(api, database: PgConfig, embedder, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    firm, adv, _ = await _firm(api, database)

    async def down(*a, **k):  # type: ignore[no-untyped-def]
        raise EmbeddingError("Voyage error 529: overloaded")

    monkeypatch.setattr(embedder, "embed_query", down)
    for r in (await api.post(f"/v1/firms/{firm}/search", json={"query": "notice of claim"}, headers=adv),
              await api.get("/v1/corpus/search", params={"q": "notice of claim"}, headers=adv)):
        assert r.status_code == 503 and r.json()["code"] == "search_unavailable" and r.headers["retry-after"] == "10"
        assert "Voyage" not in r.text and "Traceback" not in r.text  # provider details stay in the logs


async def test_storage_outage_on_upload_is_a_503_and_leaves_nothing_behind(api, database: PgConfig, storage, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await _firm(api, database)

    async def down(*a, **k):  # type: ignore[no-untyped-def]
        raise StorageError("S3 endpoint unreachable")

    monkeypatch.setattr(storage, "put", down)
    r = await api.post(f"/v1/firms/{firm}/clients/{cid}/documents", headers=adv, **files("p.txt", b"A policy document with enough text in it."))
    assert r.status_code == 503 and r.json()["code"] == "storage_unavailable"
    assert owner_sql(database, "SELECT count(*) FROM documents WHERE firm_id=%s", firm)[0][0] == 0  # no half-created document


async def test_missing_stored_file_is_a_404_not_a_500(api, database: PgConfig, storage) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await _firm(api, database)
    doc = (await api.post(f"/v1/firms/{firm}/clients/{cid}/documents", headers=adv, **files("p.txt", b"A policy document with enough text in it."))).json()
    for p in storage.root.rglob("original"):
        p.unlink()
    assert (await api.get(f"/v1/firms/{firm}/documents/{doc['id']}/content", headers=adv)).status_code == 404


async def test_the_api_keeps_serving_after_dependency_failures(api, database: PgConfig, embedder, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    firm, adv, _ = await _firm(api, database)

    async def down(*a, **k):  # type: ignore[no-untyped-def]
        raise EmbeddingError("down")

    monkeypatch.setattr(embedder, "embed_query", down)
    for _ in range(5):
        assert (await api.post(f"/v1/firms/{firm}/search", json={"query": "x"}, headers=adv)).status_code == 503
    assert (await api.get("/v1/health")).status_code == 200  # failures are contained to the request
    _, h, _ = await make_user(api, "still-works")
    assert (await api.get("/v1/me", headers=h)).status_code == 200
