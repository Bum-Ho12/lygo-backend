"""Phase B end-to-end: platform bootstrap, invitations, members, clients, matters, deadlines, tasks."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import PgConfig
from tests.integration.helpers import firm_member, make_user, new_firm, owner_sql, token_from

pytestmark = pytest.mark.asyncio
DUE = (datetime.now(UTC) + timedelta(days=14)).isoformat()


async def portal_user(api, pg, outbox, firm, advocate_headers, client_id, role="client_user"):  # type: ignore[no-untyped-def]
    uid, headers, email = await make_user(api, "portal")
    r = await api.post(f"/v1/firms/{firm}/clients/{client_id}/invitations", json={"email": email, "role": role}, headers=advocate_headers)
    assert r.status_code == 201, r.text
    acc = await api.post("/v1/invitations/accept", json={"token": token_from(outbox, email)}, headers=headers)
    assert acc.status_code == 200, acc.text
    return uid, headers


async def new_client(api, firm, headers, name="Acme"):  # type: ignore[no-untyped-def]
    r = await api.post(f"/v1/firms/{firm}/clients", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- platform bootstrap + invitation acceptance --------------------------------------------------
async def test_platform_creates_firm_and_owner_accepts(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    pid, ph, _ = await make_user(api, "platform")
    owner_sql(database, "UPDATE users SET platform_role='platform_admin' WHERE id=%s", pid)
    _oid, oh, owner_email = await make_user(api, "owner")
    slug = f"firm-{uuid.uuid4().hex[:8]}"

    assert (await api.post("/v1/platform/firms", json={"slug": slug, "name": "N", "owner_email": owner_email}, headers=oh)).status_code == 403
    r = await api.post("/v1/platform/firms", json={"slug": slug, "name": "New Firm", "owner_email": owner_email}, headers=ph)
    assert r.status_code == 201, r.text
    firm = r.json()["id"]
    assert (await api.post("/v1/platform/firms", json={"slug": slug, "name": "dup", "owner_email": owner_email}, headers=ph)).status_code == 409
    assert slug in [f["slug"] for f in (await api.get("/v1/platform/firms", headers=ph)).json()["items"]]

    assert (await api.get(f"/v1/firms/{firm}", headers=oh)).status_code == 404  # not yet a member
    acc = await api.post("/v1/invitations/accept", json={"token": token_from(outbox, owner_email)}, headers=oh)
    assert acc.status_code == 200 and acc.json()["role"] == "firm_owner"
    assert (await api.get(f"/v1/firms/{firm}", headers=oh)).json()["name"] == "New Firm"
    assert (await api.get(f"/v1/firms/{firm}", headers=ph)).status_code == 404  # platform has no tenant access
    assert (await api.post("/v1/invitations/accept", json={"token": token_from(outbox, owner_email)}, headers=oh)).status_code == 400  # single use


async def test_invitation_security(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, owner, _ = await firm_member(api, database, firm, "firm_owner")
    _, thief, _ = await make_user(api, "thief")
    _, invitee, email = await make_user(api, "invitee")
    assert (await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)).status_code == 201
    token = token_from(outbox, email)

    assert (await api.post("/v1/invitations/accept", json={"token": token}, headers=thief)).status_code == 400  # email mismatch
    assert (await api.post("/v1/invitations/accept", json={"token": "x" * 40}, headers=invitee)).status_code == 400
    assert (await api.post("/v1/invitations/accept", json={"token": token})).status_code == 401
    owner_sql(database, "UPDATE invitations SET expires_at = now() - interval '1 day' WHERE firm_id=%s", firm)
    assert (await api.post("/v1/invitations/accept", json={"token": token}, headers=invitee)).status_code == 400  # expired
    # the failed attempts must not have granted anything
    assert (await api.get(f"/v1/firms/{firm}", headers=invitee)).status_code == 404


# --- members -------------------------------------------------------------------------------------
async def test_member_management_and_last_owner_protection(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    oid, owner, _ = await firm_member(api, database, firm, "firm_owner")
    aid, admin, _ = await firm_member(api, database, firm, "firm_admin")
    pid, para, _ = await firm_member(api, database, firm, "paralegal")

    members = (await api.get(f"/v1/firms/{firm}/members", headers=para)).json()["items"]
    assert {m["role"] for m in members} == {"firm_owner", "firm_admin", "paralegal"}
    assert (await api.post(f"/v1/firms/{firm}/invitations", json={"email": "x@example.com", "role": "advocate"}, headers=para)).status_code == 403
    assert (await api.post(f"/v1/firms/{firm}/invitations", json={"email": "x@example.com", "role": "firm_owner"}, headers=admin)).status_code == 403
    assert (await api.post(f"/v1/firms/{firm}/invitations", json={"email": "x@example.com", "role": "client_user"}, headers=owner)).status_code == 422

    # last owner cannot be demoted, suspended or removed; admins cannot touch owners
    assert (await api.patch(f"/v1/firms/{firm}/members/{oid}", json={"role": "advocate"}, headers=owner)).status_code == 409
    assert (await api.patch(f"/v1/firms/{firm}/members/{oid}", json={"status": "suspended"}, headers=owner)).status_code == 409
    assert (await api.delete(f"/v1/firms/{firm}/members/{oid}", headers=owner)).status_code == 409
    assert (await api.patch(f"/v1/firms/{firm}/members/{oid}", json={"role": "advocate"}, headers=admin)).status_code == 403
    assert (await api.patch(f"/v1/firms/{firm}/members/{pid}", json={"role": "firm_owner"}, headers=admin)).status_code == 403

    # promote a second owner; now the first can step down
    assert (await api.patch(f"/v1/firms/{firm}/members/{aid}", json={"role": "firm_owner"}, headers=owner)).status_code == 200
    assert (await api.patch(f"/v1/firms/{firm}/members/{oid}", json={"role": "advocate"}, headers=owner)).status_code == 200
    # a suspended member loses access
    assert (await api.patch(f"/v1/firms/{firm}/members/{pid}", json={"status": "suspended"}, headers=admin)).status_code == 200
    assert (await api.get(f"/v1/firms/{firm}", headers=para)).status_code == 404
    assert (await api.delete(f"/v1/firms/{firm}/members/{pid}", headers=admin)).status_code == 204
    assert (await api.delete(f"/v1/firms/{firm}/members/{uuid.uuid4()}", headers=admin)).status_code == 404
    actions = {r[0] for r in owner_sql(database, "SELECT action FROM audit_events WHERE firm_id=%s", firm)}
    assert {"member.update", "member.remove"} <= actions


async def test_members_listing_never_crosses_firms(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    f1, f2 = new_firm(database), new_firm(database)
    _, h1, e1 = await firm_member(api, database, f1, "advocate")
    _, _h2, e2 = await firm_member(api, database, f2, "advocate")
    emails = {m["email"] for m in (await api.get(f"/v1/firms/{f1}/members", headers=h1)).json()["items"]}
    assert emails == {e1} and e2 not in emails


# --- clients + portal ------------------------------------------------------------------------------
async def test_client_crud_and_permissions(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, adv, _ = await firm_member(api, database, firm, "advocate")
    _, view, _ = await firm_member(api, database, firm, "viewer")
    cid = await new_client(api, firm, adv)
    assert (await api.post(f"/v1/firms/{firm}/clients", json={"name": "x"}, headers=view)).status_code == 403
    assert (await api.post(f"/v1/firms/{firm}/clients", json={"name": ""}, headers=adv)).status_code == 422
    assert (await api.post(f"/v1/firms/{firm}/clients", json={"name": "x", "type": "alien"}, headers=adv)).status_code == 422
    assert (await api.get(f"/v1/firms/{firm}/clients/{cid}", headers=view)).json()["name"] == "Acme"
    assert (await api.patch(f"/v1/firms/{firm}/clients/{cid}", json={"sensitivity": "sensitive"}, headers=adv)).json()["sensitivity"] == "sensitive"
    assert (await api.delete(f"/v1/firms/{firm}/clients/{cid}", headers=adv)).status_code == 204
    assert (await api.get(f"/v1/firms/{firm}/clients/{cid}", headers=adv)).status_code == 404
    assert (await api.get(f"/v1/firms/{firm}/clients", headers=adv)).json()["items"] == []


async def test_cross_firm_client_access_is_404(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    f1, f2 = new_firm(database), new_firm(database)
    _, h1, _ = await firm_member(api, database, f1, "advocate")
    _, h2, _ = await firm_member(api, database, f2, "advocate")
    cid2 = await new_client(api, f2, h2)
    assert (await api.get(f"/v1/firms/{f1}/clients/{cid2}", headers=h1)).status_code == 404  # RLS: not visible via own firm
    assert (await api.patch(f"/v1/firms/{f1}/clients/{cid2}", json={"name": "x"}, headers=h1)).status_code == 404
    assert (await api.get(f"/v1/firms/{f2}/clients/{cid2}", headers=h1)).status_code == 404  # not a member of f2


async def test_portal_user_isolation_and_limits(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, adv, _ = await firm_member(api, database, firm, "advocate")
    c1, c2 = await new_client(api, firm, adv, "C1"), await new_client(api, firm, adv, "C2")
    m1 = (await api.post(f"/v1/firms/{firm}/clients/{c1}/matters", json={"title": "M1"}, headers=adv)).json()["id"]
    m2 = (await api.post(f"/v1/firms/{firm}/clients/{c2}/matters", json={"title": "M2"}, headers=adv)).json()["id"]
    d1 = (await api.post(f"/v1/firms/{firm}/matters/{m1}/deadlines", json={"title": "File", "due_at": DUE}, headers=adv)).json()["id"]
    await api.post(f"/v1/firms/{firm}/matters/{m2}/deadlines", json={"title": "Other", "due_at": DUE}, headers=adv)
    await api.post(f"/v1/firms/{firm}/tasks", json={"title": "internal", "matter_id": m1}, headers=adv)

    _, portal = await portal_user(api, database, outbox, firm, adv, c1)
    base = f"/v1/firms/{firm}"
    assert [c["name"] for c in (await api.get(f"{base}/clients", headers=portal)).json()["items"]] == ["C1"]
    assert (await api.get(f"{base}/clients/{c2}", headers=portal)).status_code == 404
    assert [m["title"] for m in (await api.get(f"{base}/clients/{c1}/matters", headers=portal)).json()["items"]] == ["M1"]
    assert (await api.get(f"{base}/clients/{c2}/matters", headers=portal)).json()["items"] == []
    assert (await api.get(f"{base}/matters/{m2}", headers=portal)).status_code == 404
    assert [d["id"] for d in (await api.get(f"{base}/matters/{m1}/deadlines", headers=portal)).json()["items"]] == [d1]
    assert (await api.get(f"{base}/matters/{m2}/deadlines", headers=portal)).status_code == 404
    # firm-only things stay firm-only
    assert (await api.get(f"{base}/tasks", headers=portal)).status_code == 403
    assert (await api.get(f"{base}/members", headers=portal)).status_code == 403
    assert (await api.post(f"{base}/clients", json={"name": "x"}, headers=portal)).status_code == 403
    assert (await api.post(f"{base}/matters/{m1}/deadlines", json={"title": "x", "due_at": DUE}, headers=portal)).status_code == 403
    assert (await api.patch(f"{base}/matters/{m1}", json={"status": "closed"}, headers=portal)).status_code == 403


# --- matters, deadlines, tasks -----------------------------------------------------------------------
async def test_matters_lifecycle_and_assignee_validation(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, other = new_firm(database), new_firm(database)
    aid, adv, _ = await firm_member(api, database, firm, "advocate")
    stranger, _, _ = await firm_member(api, database, other, "advocate")
    cid = await new_client(api, firm, adv)
    url = f"/v1/firms/{firm}/clients/{cid}/matters"
    assert (await api.post(url, json={"title": "bad", "assigned_user_ids": [str(uuid.uuid4())]}, headers=adv)).status_code == 422
    assert (await api.post(url, json={"title": "bad", "assigned_user_ids": [str(stranger)]}, headers=adv)).status_code == 422  # member of another firm
    r = await api.post(url, json={"title": "Policy dispute", "practice_area": "insurance", "assigned_user_ids": [str(aid)]}, headers=adv)
    assert r.status_code == 201 and r.json()["assigned_user_ids"] == [str(aid)]
    mid = r.json()["id"]
    assert (await api.patch(f"/v1/firms/{firm}/matters/{mid}", json={"status": "closed"}, headers=adv)).json()["status"] == "closed"
    assert (await api.patch(f"/v1/firms/{firm}/matters/{mid}", json={"status": "weird"}, headers=adv)).status_code == 422
    assert (await api.post(f"/v1/firms/{firm}/clients/{uuid.uuid4()}/matters", json={"title": "x"}, headers=adv)).status_code == 404


async def test_deadlines_and_tasks(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    aid, adv, _ = await firm_member(api, database, firm, "advocate")
    cid = await new_client(api, firm, adv)
    mid = (await api.post(f"/v1/firms/{firm}/clients/{cid}/matters", json={"title": "M"}, headers=adv)).json()["id"]
    m2 = (await api.post(f"/v1/firms/{firm}/clients/{cid}/matters", json={"title": "M2"}, headers=adv)).json()["id"]
    base = f"/v1/firms/{firm}"
    assert (await api.post(f"{base}/matters/{mid}/deadlines", json={"title": "x", "due_at": "2026-12-01T10:00:00"}, headers=adv)).status_code == 422  # naive datetime
    did = (await api.post(f"{base}/matters/{mid}/deadlines", json={"title": "Reply", "kind": "limitation", "due_at": DUE}, headers=adv)).json()["id"]
    assert (await api.patch(f"{base}/matters/{mid}/deadlines/{did}", json={"status": "done"}, headers=adv)).json()["status"] == "done"
    assert (await api.patch(f"{base}/matters/{m2}/deadlines/{did}", json={"status": "open"}, headers=adv)).status_code == 404  # wrong matter

    t = await api.post(f"{base}/tasks", json={"title": "Review policy", "matter_id": mid, "assignee_id": str(aid)}, headers=adv)
    assert t.status_code == 201 and t.json()["client_id"] == cid
    assert (await api.post(f"{base}/tasks", json={"title": "x", "assignee_id": str(uuid.uuid4())}, headers=adv)).status_code == 422
    tid = t.json()["id"]
    assert (await api.patch(f"{base}/tasks/{tid}", json={"status": "done"}, headers=adv)).json()["status"] == "done"
    assert [x["id"] for x in (await api.get(f"{base}/tasks?status=done", headers=adv)).json()["items"]] == [tid]
    assert (await api.get(f"{base}/tasks?status=open", headers=adv)).json()["items"] == []
    actions = {r[0] for r in owner_sql(database, "SELECT action FROM audit_events WHERE firm_id=%s", firm)}
    assert {"client.create", "matter.create", "deadline.create", "deadline.update", "task.create", "task.update"} <= actions
