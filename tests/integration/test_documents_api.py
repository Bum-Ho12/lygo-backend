"""Phase C end-to-end: both ingest doors, the pipeline, flags, versions, comparison and isolation."""
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.ingestion.scan import EICAR
from tests.conftest import PgConfig
from tests.integration.helpers import firm_member, make_user, new_firm, owner_sql, token_from
from tests.unit.ingestion import factories as fx

pytestmark = pytest.mark.asyncio
FIX = Path(__file__).parents[1] / "fixtures"

POLICY = (
    b"MOTOR INSURANCE POLICY\n\nPolicy Schedule\nInsured: Jane Doe. Sum insured KES 2,000,000. Premium KES 40,000.\n\n"
    b"1. Exclusions. This policy does not cover any pre-existing condition.\n\n"
    b"2. Claims. You must notify us of any claim within 14 days of the event.\n"
)


def files(name: str, data: bytes, **form: str) -> dict:  # type: ignore[type-arg]
    return {"files": {"file": (name, data)}, "data": form}


async def upload_client(api, firm, cid, headers, name="policy.txt", data=POLICY, **form):  # type: ignore[no-untyped-def]
    return await api.post(f"/v1/firms/{firm}/clients/{cid}/documents", headers=headers, **files(name, data, **form))


async def setup_firm(api, pg):  # type: ignore[no-untyped-def]
    firm = new_firm(pg)
    _, adv, _ = await firm_member(api, pg, firm, "advocate")
    cid = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Acme"}, headers=adv)).json()["id"]
    return firm, adv, cid


async def portal(api, pg, outbox, firm, adv, cid):  # type: ignore[no-untyped-def]
    _, h, email = await make_user(api, "portal")
    await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": email}, headers=adv)
    assert (await api.post("/v1/invitations/accept", json={"token": token_from(outbox, email)}, headers=h)).status_code == 200
    return h


# --- happy path ----------------------------------------------------------------------------------
async def test_client_upload_is_processed_classified_and_flagged(api, database: PgConfig, storage) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    r = await upload_client(api, firm, cid, adv, tags="Motor, urgent")
    assert r.status_code == 201, r.text
    assert r.json()["tags"] == ["motor", "urgent"] and r.json()["duplicate"] is False
    doc = (await api.get(f"/v1/firms/{firm}/documents/{r.json()['id']}", headers=adv)).json()
    assert doc["status"] == "processed" and doc["doc_type"] == "insurance_policy" and doc["practice_area"] == "insurance"
    assert [e["stage"] for e in doc["events"]] == ["start", "parse", "structure", "classify", "rules", "index"]
    assert all(e["status"] == "ok" for e in doc["events"])
    flags = (await api.get(f"/v1/firms/{firm}/documents/{doc['id']}/flags", headers=adv)).json()
    by_rule = {f["rule_id"]: f for f in flags}
    assert {"ins.exclusion.pre_existing", "ins.claim.notice_window"} <= set(by_rule)
    claim = by_rule["ins.claim.notice_window"]
    assert claim["severity"] == "critical" and "14 days" in claim["explanation"] and claim["span"]["section_number"] == "2"
    assert flags[0]["severity"] == "critical"  # most severe first
    assert doc["flag_counts"]["critical"] == 1
    # the original is stored byte-for-byte and downloadable with safe headers
    dl = await api.get(f"/v1/firms/{firm}/documents/{doc['id']}/content", headers=adv)
    assert dl.content == POLICY and dl.headers["x-content-type-options"] == "nosniff" and "attachment" in dl.headers["content-disposition"]
    assert any(p.is_file() for p in storage.root.rglob("original"))


async def test_duplicate_upload_returns_existing_document(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    first = (await upload_client(api, firm, cid, adv)).json()
    again = await upload_client(api, firm, cid, adv, name="renamed.txt")
    assert again.status_code == 200 and again.json()["id"] == first["id"] and again.json()["duplicate"] is True
    assert len((await api.get(f"/v1/firms/{firm}/clients/{cid}/documents", headers=adv)).json()["items"]) == 1
    other = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Other"}, headers=adv)).json()["id"]
    assert (await upload_client(api, firm, other, adv)).status_code == 201  # same bytes, different client: allowed


# --- rejection paths -----------------------------------------------------------------------------
async def test_uploads_are_rejected_before_storage(api, database: PgConfig, storage, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    quarantined = await upload_client(api, firm, cid, adv, name="x.txt", data=b"hello " + EICAR)
    assert quarantined.status_code == 422 and quarantined.json()["code"] == "malware_detected"
    assert (await upload_client(api, firm, cid, adv, name="fake.pdf", data=fx.png_bytes())).status_code == 415
    assert (await upload_client(api, firm, cid, adv, name="tool.exe", data=b"MZ" + b"\x00" * 50)).status_code == 415
    assert (await upload_client(api, firm, cid, adv, name="e.txt", data=b"")).status_code == 422
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    assert (await upload_client(api, firm, cid, adv, name="big.txt", data=b"abc")).status_code == 413
    assert not list(storage.root.rglob("original"))  # nothing was stored
    actions = {r[0] for r in owner_sql(database, "SELECT action FROM audit_events WHERE firm_id=%s", firm)}
    assert "document.quarantined" in actions and "document.upload" not in actions


# --- OCR / review paths --------------------------------------------------------------------------
async def test_scanned_pdf_low_confidence_goes_to_review_queue(api, database: PgConfig, ocr) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    ocr.text, ocr.confidence = "policy schedule sum insured premium insurance exclusions", 0.5
    doc = (await upload_client(api, firm, cid, adv, name="scan.pdf", data=fx.pdf_scanned(2))).json()
    d = (await api.get(f"/v1/firms/{firm}/documents/{doc['id']}", headers=adv)).json()
    assert d["status"] == "needs_review" and d["page_count"] == 2 and "low_confidence_pages=[1, 2]" in d["status_detail"]
    q = (await api.get(f"/v1/firms/{firm}/review-queue", headers=adv)).json()["items"]
    assert [(i["document_id"], i["page"]) for i in q] == [(doc["id"], 1), (doc["id"], 2)]


async def test_ocr_unavailable_and_password_protected(api, database: PgConfig, ocr) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    ocr.available = False
    d1 = (await upload_client(api, firm, cid, adv, name="s.pdf", data=fx.pdf_scanned(1))).json()
    got = (await api.get(f"/v1/firms/{firm}/documents/{d1['id']}", headers=adv)).json()
    assert got["status"] == "needs_review" and "ocr_unavailable" in got["status_detail"]
    d2 = (await upload_client(api, firm, cid, adv, name="l.pdf", data=fx.pdf_encrypted())).json()
    got = (await api.get(f"/v1/firms/{firm}/documents/{d2['id']}", headers=adv)).json()
    assert got["status"] == "needs_review" and got["status_detail"] == "password_protected"


async def test_pipeline_failure_is_recorded_not_raised(api, database: PgConfig, storage) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    doc = (await upload_client(api, firm, cid, adv)).json()
    for p in storage.root.rglob("original"):
        p.unlink()  # simulate lost object
    assert (await api.post(f"/v1/firms/{firm}/documents/{doc['id']}:reprocess", headers=adv)).status_code == 202
    d = (await api.get(f"/v1/firms/{firm}/documents/{doc['id']}", headers=adv)).json()
    assert d["status"] == "failed" and "ObjectNotFound" in d["status_detail"]
    assert d["events"][-1]["status"] == "error"


# --- flags, reprocessing -------------------------------------------------------------------------
async def test_flag_review_survives_reprocessing(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    doc = (await upload_client(api, firm, cid, adv)).json()
    url = f"/v1/firms/{firm}/documents/{doc['id']}/flags"
    flags = (await api.get(url, headers=adv)).json()
    keep, drop = flags[0], flags[1]
    assert (await api.patch(f"/v1/firms/{firm}/flags/{drop['id']}", json={"status": "dismissed"}, headers=adv)).status_code == 422  # reason required
    assert (await api.patch(f"/v1/firms/{firm}/flags/{keep['id']}", json={"status": "acknowledged"}, headers=adv)).json()["status"] == "acknowledged"
    assert (await api.patch(f"/v1/firms/{firm}/flags/{drop['id']}", json={"status": "dismissed", "note": "not relevant"}, headers=adv)).status_code == 200
    await api.post(f"/v1/firms/{firm}/documents/{doc['id']}:reprocess", headers=adv)
    after = (await api.get(url, headers=adv)).json()
    assert len(after) == len(flags)  # no duplicates, nothing lost
    assert {f["rule_id"]: f["status"] for f in after} == {keep["rule_id"]: "acknowledged", drop["rule_id"]: "dismissed"} | {
        f["rule_id"]: "open" for f in flags[2:]
    }


# --- versions + comparison -----------------------------------------------------------------------
async def test_versions_and_comparison(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    v1 = (await upload_client(api, firm, cid, adv, name="c.txt", data=(FIX / "contract_v1.txt").read_bytes())).json()
    v2 = (await upload_client(api, firm, cid, adv, name="c2.txt", data=(FIX / "contract_v2.txt").read_bytes(), previous_version_id=v1["id"])).json()
    assert v2["version_group_id"] == v1["version_group_id"] and (v1["version_no"], v2["version_no"]) == (1, 2)
    r = await api.post(f"/v1/firms/{firm}/comparisons", json={"left_document_id": v1["id"], "right_document_id": v2["id"]}, headers=adv)
    assert r.status_code == 200, r.text
    items = {i["heading"]: i for i in r.json()["result"]["items"]}
    assert items["Payment"]["change"] == "modified" and items["Term"]["change"] == "added" and items["Governing law"]["change"] == "removed"
    assert (await upload_client(api, firm, cid, adv, name="x.txt", data=b"unrelated document body here", previous_version_id=str(uuid.uuid4()))).status_code == 422
    # comparison is a firm-only tool
    ph = await portal(api, database, outbox, firm, adv, cid)
    assert (await api.post(f"/v1/firms/{firm}/comparisons", json={"left_document_id": v1["id"], "right_document_id": v2["id"]}, headers=ph)).status_code == 403
    assert (await api.post(f"/v1/firms/{firm}/comparisons", json={"left_document_id": v1["id"], "right_document_id": str(uuid.uuid4())}, headers=adv)).status_code == 404


# --- isolation + permissions ---------------------------------------------------------------------
async def test_client_portal_documents_and_flags_isolated(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, c1 = await setup_firm(api, database)
    c2 = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Sibling"}, headers=adv)).json()["id"]
    d1 = (await upload_client(api, firm, c1, adv, data=POLICY)).json()
    d2 = (await upload_client(api, firm, c2, adv, data=POLICY + b"\nExtra clause for uniqueness.\n")).json()
    ph = await portal(api, database, outbox, firm, adv, c1)
    base = f"/v1/firms/{firm}"
    assert [d["id"] for d in (await api.get(f"{base}/clients/{c1}/documents", headers=ph)).json()["items"]] == [d1["id"]]
    assert (await api.get(f"{base}/clients/{c2}/documents", headers=ph)).json()["items"] == []
    assert (await api.get(f"{base}/documents/{d2['id']}", headers=ph)).status_code == 404
    assert (await api.get(f"{base}/documents/{d2['id']}/content", headers=ph)).status_code == 404
    assert len((await api.get(f"{base}/documents/{d1['id']}/flags", headers=ph)).json()) >= 2  # may see own flags
    flag = (await api.get(f"{base}/documents/{d1['id']}/flags", headers=adv)).json()[0]
    assert (await api.patch(f"{base}/flags/{flag['id']}", json={"status": "acknowledged"}, headers=ph)).status_code == 403
    assert (await upload_client(api, firm, c2, ph, name="n.txt", data=b"sibling upload attempt text")).status_code == 404
    own = await upload_client(api, firm, c1, ph, name="mine.txt", data=b"my own upload by the client herself")
    assert own.status_code == 201 and own.json()["client_id"] == c1
    assert (await api.post(f"{base}/documents/{d1['id']}:reprocess", headers=ph)).status_code == 403
    assert (await api.get(f"{base}/review-queue", headers=ph)).status_code == 403


async def test_knowledge_base_door_and_sharing(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    _, view, _ = await firm_member(api, database, firm, "viewer")
    ph = await portal(api, database, outbox, firm, adv, cid)
    base = f"/v1/firms/{firm}"
    kb = await api.post(f"{base}/knowledge/documents", headers=adv, **files("precedent.txt", b"Precedent: standard service agreement terms for the firm.", title="Precedent"))
    assert kb.status_code == 201 and kb.json()["scope"] == "firm_kb" and kb.json()["client_id"] is None
    assert (await api.post(f"{base}/knowledge/documents", headers=view, **files("p.txt", b"viewer upload text here"))).status_code == 403
    assert (await api.post(f"{base}/knowledge/documents", headers=ph, **files("p.txt", b"portal upload text here"))).status_code == 403
    flags = (await api.get(f"{base}/documents/{kb.json()['id']}/flags", headers=adv)).json()
    assert flags == []  # rules only run on client documents
    # invisible to clients until the firm shares it
    assert (await api.get(f"{base}/knowledge/documents", headers=ph)).json()["items"] == []
    assert (await api.get(f"{base}/documents/{kb.json()['id']}", headers=ph)).status_code == 404
    assert (await api.patch(f"{base}/knowledge/documents/{kb.json()['id']}", json={"shared_with_clients": True}, headers=ph)).status_code == 403
    assert (await api.patch(f"{base}/knowledge/documents/{kb.json()['id']}", json={"shared_with_clients": True, "tags": ["Template"]}, headers=adv)).json()["tags"] == ["template"]
    assert [d["title"] for d in (await api.get(f"{base}/knowledge/documents", headers=ph)).json()["items"]] == ["Precedent"]
    assert (await api.get(f"{base}/documents/{kb.json()['id']}/content", headers=ph)).status_code == 200
    # clients' own documents never appear in the KB listing
    await upload_client(api, firm, cid, adv)
    assert len((await api.get(f"{base}/knowledge/documents", headers=adv)).json()["items"]) == 1


async def test_cross_firm_document_access_is_404(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    f1, a1, c1 = await setup_firm(api, database)
    f2, a2, _ = await setup_firm(api, database)
    doc = (await upload_client(api, f1, c1, a1)).json()
    assert (await api.get(f"/v1/firms/{f2}/documents/{doc['id']}", headers=a2)).status_code == 404
    assert (await api.get(f"/v1/firms/{f1}/documents/{doc['id']}", headers=a2)).status_code == 404
    assert (await api.post(f"/v1/firms/{f2}/comparisons", json={"left_document_id": doc["id"], "right_document_id": doc["id"]}, headers=a2)).status_code == 404
