import asyncio
import json
import uuid

import pytest

from tests.conftest import PgConfig
from tests.integration.helpers import firm_member, make_user, new_firm, owner_sql, token_from
from tests.integration.test_documents_api import files, setup_firm, upload_client

pytestmark = pytest.mark.asyncio


def policy(insured: str, sum_insured: str, ref: str) -> bytes:
    return (
        f"MOTOR INSURANCE POLICY\n\nPolicy No: {ref}\nInsured: {insured}\nInsurer: Acme Insurance Ltd\n\n"
        f"1. Cover. Sum insured KES {sum_insured}. Premium KES 40,000 payable within 30 days.\n\n"
        "2. Exclusions. This policy does not cover any pre-existing condition.\n\n"
        "3. Claims. You must notify us of any claim within 14 days of the event.\n"
    ).encode()


async def portal(api, outbox, firm, adv, cid):  # type: ignore[no-untyped-def]
    _, h, email = await make_user(api, "portal")
    await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": email}, headers=adv)
    await api.post("/v1/invitations/accept", json={"token": token_from(outbox, email)}, headers=h)
    return h


async def test_fields_are_stored_and_compared_across_documents(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, adv, c1 = await setup_firm(api, database)
    c2 = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Other"}, headers=adv)).json()["id"]
    a = (await upload_client(api, firm, c1, adv, name="a.txt", data=policy("Jane Wanjiru", "2,000,000", "POL/1"))).json()
    b = (await upload_client(api, firm, c2, adv, name="b.txt", data=policy("Joe Otieno", "3,000,000", "POL/2"))).json()
    fields = (await api.get(f"/v1/firms/{firm}/documents/{a['id']}/fields", headers=adv)).json()
    assert {"kind": "money", "key": "sum insured", "value": "KES 2,000,000", "section_path": "1"} in fields
    assert any(f["kind"] == "party" and f["key"] == "insured" and f["value"] == "Jane Wanjiru" for f in fields)
    doc = (await api.get(f"/v1/firms/{firm}/documents/{a['id']}", headers=adv)).json()
    assert "extract" in [e["stage"] for e in doc["events"]]

    r = (await api.post(f"/v1/firms/{firm}/comparisons", json={"left_document_id": a["id"], "right_document_id": b["id"]}, headers=adv)).json()
    diff = {(d["kind"], d["key"]): d for d in r["field_diff"]}
    assert diff[("money", "sum insured")]["left"] == ["kes 2000000"] and diff[("money", "sum insured")]["right"] == ["kes 3000000"]
    assert diff[("party", "insured")]["status"] == "changed" and diff[("reference", "policy_number")]["status"] == "changed"
    assert ("money", "premium") not in diff and ("party", "insurer") not in diff  # identical across the two policies
    # cross-client comparison is allowed for the firm; the client portal never sees it (compare.run)


async def test_reprocessing_replaces_fields_and_entity_mentions(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    d = (await upload_client(api, firm, cid, adv, data=policy("Jane Wanjiru", "2,000,000", "POL/9"))).json()
    count = lambda t: owner_sql(database, f"SELECT count(*) FROM {t} WHERE document_id = %s", d["id"])[0][0]
    n_fields, n_mentions = count("document_fields"), count("entity_mentions")
    assert n_fields > 5 and n_mentions >= 3
    await api.post(f"/v1/firms/{firm}/documents/{d['id']}:reprocess", headers=adv)
    assert (count("document_fields"), count("entity_mentions")) == (n_fields, n_mentions)
    assert owner_sql(database, "SELECT count(*) FROM entities WHERE firm_id=%s AND name_norm='jane wanjiru'", firm)[0][0] == 1


async def test_entities_span_clients_but_stay_inside_the_firm(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, c1 = await setup_firm(api, database)
    c2 = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Other"}, headers=adv)).json()["id"]
    d1 = (await upload_client(api, firm, c1, adv, name="1.txt", data=policy("Jane Wanjiru", "1,000,000", "POL/A"))).json()
    d2 = (await upload_client(api, firm, c2, adv, name="2.txt", data=policy("Jane Wanjiru", "5,000,000", "POL/B"))).json()
    ents = (await api.get(f"/v1/firms/{firm}/entities", params={"q": "jane"}, headers=adv)).json()
    assert len(ents) == 1 and ents[0]["mentions"] == 2 and set(ents[0]["documents"]) == {d1["id"], d2["id"]}  # same person, two clients: conflict-check seed
    acme = (await api.get(f"/v1/firms/{firm}/entities", params={"q": "acme"}, headers=adv)).json()
    assert acme and acme[0]["kind"] == "org"
    # other firms and client users see nothing
    other = new_firm(database)
    _, oadv, _ = await firm_member(api, database, other, "advocate")
    assert (await api.get(f"/v1/firms/{other}/entities", headers=oadv)).json() == []
    ph = await portal(api, outbox, firm, adv, c1)
    assert (await api.get(f"/v1/firms/{firm}/entities", headers=ph)).status_code == 403
    # but the portal user can read the fields of their own document, and not the sibling's
    assert (await api.get(f"/v1/firms/{firm}/documents/{d1['id']}/fields", headers=ph)).status_code == 200
    assert (await api.get(f"/v1/firms/{firm}/documents/{d2['id']}/fields", headers=ph)).status_code == 404


def parse_sse(text: str) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    out = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            out.append((fields["event"], json.loads(fields["data"])))
    return out


async def test_sse_streams_the_timeline_and_ends(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, adv, cid = await setup_firm(api, database)
    d = (await upload_client(api, firm, cid, adv)).json()
    r = await api.get(f"/v1/firms/{firm}/documents/{d['id']}/events", headers=adv)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert [e[1]["stage"] for e in events if e[0] == "stage"] == ["start", "parse", "structure", "classify", "extract", "rules", "index"]
    assert events[-1] == ("done", {"status": "processed"})
    first_id = int(r.text.split("id: ")[1].split("\n")[0])
    resumed = parse_sse((await api.get(f"/v1/firms/{firm}/documents/{d['id']}/events", params={"after": first_id}, headers=adv)).text)
    assert "start" not in [e[1].get("stage") for e in resumed] and resumed[-1][0] == "done"  # resumes after the given event id


async def test_sse_waits_for_a_running_document(api, database: PgConfig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import settings

    monkeypatch.setattr(settings, "sse_poll_seconds", 0.05)
    firm, adv, cid = await setup_firm(api, database)
    d = (await upload_client(api, firm, cid, adv)).json()
    owner_sql(database, "UPDATE documents SET status='processing' WHERE id=%s", d["id"])

    async def finish() -> None:
        await asyncio.sleep(0.3)
        owner_sql(database, "UPDATE documents SET status='processed' WHERE id=%s", d["id"])

    task = asyncio.create_task(finish())
    r = await asyncio.wait_for(api.get(f"/v1/firms/{firm}/documents/{d['id']}/events", headers=adv), timeout=10)
    await task
    assert parse_sse(r.text)[-1] == ("done", {"status": "processed"})


async def test_sse_respects_isolation(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, adv, c1 = await setup_firm(api, database)
    c2 = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Other"}, headers=adv)).json()["id"]
    mine = (await upload_client(api, firm, c1, adv, name="m.txt", data=policy("A", "1", "P1"))).json()
    theirs = (await upload_client(api, firm, c2, adv, name="t.txt", data=policy("B", "2", "P2"))).json()
    ph = await portal(api, outbox, firm, adv, c1)
    assert (await api.get(f"/v1/firms/{firm}/documents/{mine['id']}/events", headers=ph)).status_code == 200
    assert (await api.get(f"/v1/firms/{firm}/documents/{theirs['id']}/events", headers=ph)).status_code == 404
    other = new_firm(database)
    _, oadv, _ = await firm_member(api, database, other, "advocate")
    assert (await api.get(f"/v1/firms/{other}/documents/{mine['id']}/events", headers=oadv)).status_code == 404
    assert (await api.get(f"/v1/firms/{firm}/documents/{uuid.uuid4()}/events", headers=adv)).status_code == 404
    assert files  # shared helper import
