"""What a client (portal user) may see: finished documents and their own uploads; never the firm's work in progress."""
import pytest

from tests.conftest import PgConfig
from tests.integration.helpers import owner_sql
from tests.integration.test_documents_api import POLICY, portal, setup_firm, upload_client

pytestmark = pytest.mark.asyncio


async def test_clients_see_only_finished_documents_and_their_own_uploads(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    base = f"/v1/firms/{firm}"
    done = (await upload_client(api, firm, cid, adv, data=POLICY)).json()
    wip = (await upload_client(api, firm, cid, adv, name="draft.txt", data=b"Internal draft letter to the insurer, not final.")).json()
    ph = await portal(api, database, outbox, firm, adv, cid)
    for status in ("received", "processing", "needs_review", "failed"):
        owner_sql(database, "UPDATE documents SET status=%s, status_detail='OCR page 2 low confidence' WHERE id=%s", status, wip["id"])
        listed = [d["id"] for d in (await api.get(f"{base}/clients/{cid}/documents", headers=ph)).json()["items"]]
        assert wip["id"] not in listed and done["id"] in listed, status
        for path in ("", "/content", "/sections", "/flags"):
            assert (await api.get(f"{base}/documents/{wip['id']}{path}", headers=ph)).status_code == 404, (status, path)
    # the firm still sees everything
    assert wip["id"] in [d["id"] for d in (await api.get(f"{base}/clients/{cid}/documents", headers=adv)).json()["items"]]

    detail = (await api.get(f"{base}/documents/{done['id']}", headers=ph)).json()
    assert detail["events"] == [] and detail["status_detail"] == "" and detail["tags"] == [] and detail["flag_counts"] == {}

    # the client's own upload is visible to them at once, as simply "received" (no pipeline detail)
    own = (await upload_client(api, firm, cid, ph, name="mine.txt", data=b"My own scan of the accident report, uploaded by me.")).json()
    owner_sql(database, "UPDATE documents SET status='processing', status_detail='parse: running' WHERE id=%s", own["id"])
    mine = next(d for d in (await api.get(f"{base}/clients/{cid}/documents", headers=ph)).json()["items"] if d["id"] == own["id"])
    assert mine["status"] == "received" and mine["status_detail"] == ""
    assert (await api.get(f"{base}/documents/{own['id']}/content", headers=ph)).status_code == 200


async def test_matter_staffing_is_not_shown_to_clients(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    me = (await api.get("/v1/me", headers=adv)).json()["id"]
    mid = (await api.post(f"/v1/firms/{firm}/clients/{cid}/matters", json={"title": "Claim", "assigned_user_ids": [me]}, headers=adv)).json()["id"]
    ph = await portal(api, database, outbox, firm, adv, cid)
    assert (await api.get(f"/v1/firms/{firm}/matters/{mid}", headers=ph)).json()["assigned_user_ids"] == []
    assert (await api.get(f"/v1/firms/{firm}/matters/{mid}", headers=adv)).json()["assigned_user_ids"] == [me]


async def test_sections_give_the_text_the_highlights_point_into(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    base = f"/v1/firms/{firm}"
    doc = (await upload_client(api, firm, cid, adv, data=POLICY)).json()
    sections = {s["path"]: s for s in (await api.get(f"{base}/documents/{doc['id']}/sections", headers=adv)).json()}
    flags = [f for f in (await api.get(f"{base}/documents/{doc['id']}/flags", headers=adv)).json() if f["span"] and f["span"].get("start") is not None]
    assert flags, "the policy fixture should raise at least one located flag"
    for f in flags:
        sp = f["span"]
        snippet = sections[sp["section_path"]]["full_text"][sp["start"]:sp["end"]]
        assert snippet.strip(), f  # the span lands on real text
    fields = (await api.get(f"{base}/documents/{doc['id']}/fields", headers=adv)).json()
    located = [x for x in fields if x["start"] is not None and x["section_path"] in sections]
    assert located and all(x["value"] in sections[x["section_path"]]["full_text"][x["start"]:x["end"]] or
                           sections[x["section_path"]]["full_text"][x["start"]:x["end"]] in x["value"] for x in located)


async def test_client_search_ignores_documents_the_firm_has_not_finished(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    base = f"/v1/firms/{firm}"
    doc = (await upload_client(api, firm, cid, adv, name="memo.txt", data=b"Zanzibar clause: the insurer must pay the marmalade benefit within ninety days.")).json()
    ph = await portal(api, database, outbox, firm, adv, cid)
    q = {"query": "zanzibar marmalade benefit", "scopes": ["client"]}
    assert doc["id"] in [x["citation"]["source_id"] for x in (await api.post(f"{base}/search", json=q, headers=ph)).json()]
    owner_sql(database, "UPDATE documents SET status='needs_review' WHERE id=%s", doc["id"])
    assert doc["id"] not in [x["citation"]["source_id"] for x in (await api.post(f"{base}/search", json=q, headers=ph)).json()]
    assert doc["id"] in [x["citation"]["source_id"] for x in (await api.post(f"{base}/search", json=q, headers=adv)).json()]  # firm unaffected


async def test_me_says_whether_someone_is_platform_staff(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    from tests.integration.helpers import make_user

    uid, h, _ = await make_user(api, "me-role")
    assert (await api.get("/v1/me", headers=h)).json()["platform_role"] is None
    owner_sql(database, "UPDATE users SET platform_role='platform_support' WHERE id=%s", uid)
    assert (await api.get("/v1/me", headers=h)).json()["platform_role"] == "platform_support"
