"""Invitations: no duplicates, no re-inviting existing members, and revocation (firm and client portal)."""
import uuid

import pytest

from tests.conftest import PgConfig
from tests.integration.helpers import firm_member, make_user, new_firm, owner_sql, token_from

pytestmark = pytest.mark.asyncio


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.com"


async def _firm(api, database: PgConfig):  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, owner, _ = await firm_member(api, database, firm, "firm_owner")
    _, adv, _ = await firm_member(api, database, firm, "advocate")
    cid = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Acme"}, headers=adv)).json()["id"]
    return firm, owner, adv, cid


async def test_same_email_cannot_be_invited_twice(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, owner, adv, cid = await _firm(api, database)
    email = _email("twice")
    assert (await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)).status_code == 201
    for variant in (email, email.upper(), f"  {email} "):
        r = await api.post(f"/v1/firms/{firm}/invitations", json={"email": variant.strip(), "role": "paralegal"}, headers=owner)
        assert r.status_code == 409 and r.json()["code"] == "invitation_pending", variant
    # client portal: same rule, scoped per client
    assert (await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": email}, headers=adv)).status_code == 201
    r = await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": email}, headers=adv)
    assert r.status_code == 409 and r.json()["code"] == "invitation_pending"
    other = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Other"}, headers=adv)).json()["id"]
    assert (await api.post(f"/v1/firms/{firm}/clients/{other}/invitations", json={"email": email}, headers=adv)).status_code == 201
    assert owner_sql(database, "SELECT count(*) FROM invitations WHERE firm_id=%s AND lower(email)=%s", firm, email)[0][0] == 3


async def test_existing_members_and_portal_users_are_not_reinvited(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, owner, adv, cid = await _firm(api, database)
    uid, _, email = await make_user(api, "existing")
    owner_sql(database, "INSERT INTO firm_memberships (firm_id, user_id, role) VALUES (%s, %s, 'paralegal')", firm, uid)
    r = await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "already_member"
    cuid, _, cemail = await make_user(api, "portal")
    owner_sql(database, "INSERT INTO client_memberships (client_id, firm_id, user_id, role) VALUES (%s, %s, %s, 'client_user')", cid, firm, cuid)
    r = await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": cemail}, headers=adv)
    assert r.status_code == 409 and r.json()["code"] == "already_has_access"


async def test_revoked_invitation_stops_working_and_frees_the_email(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, owner, _, _ = await _firm(api, database)
    _, invitee, email = await make_user(api, "revokee")
    inv = (await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)).json()
    token = token_from(outbox, email)
    assert (await api.delete(f"/v1/firms/{firm}/invitations/{inv['id']}", headers=owner)).status_code == 204
    assert all(i["id"] != inv["id"] for i in (await api.get(f"/v1/firms/{firm}/invitations", headers=owner)).json()["items"])
    r = await api.post("/v1/invitations/accept", json={"token": token}, headers=invitee)
    assert r.status_code == 400 and r.json()["code"] == "invalid_invitation"
    assert (await api.post(f"/v1/firms/{firm}/invitations/{inv['id']}/resend", headers=owner)).status_code == 404
    assert (await api.delete(f"/v1/firms/{firm}/invitations/{inv['id']}", headers=owner)).status_code == 404  # already revoked
    assert (await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)).status_code == 201
    assert owner_sql(database, "SELECT count(*) FROM audit_events WHERE action='invitation.revoke' AND target=%s", f"invitation:{inv['id']}")[0][0] == 1


async def test_expired_invitation_is_superseded_by_a_new_one(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, owner, _, _ = await _firm(api, database)
    email = _email("expired")
    old = (await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)).json()
    owner_sql(database, "UPDATE invitations SET expires_at = now() - interval '1 day' WHERE id=%s", old["id"])
    assert (await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)).status_code == 201
    assert owner_sql(database, "SELECT revoked_at IS NOT NULL FROM invitations WHERE id=%s", old["id"]) == [(True,)]


async def test_client_portal_invitations_can_be_listed_resent_and_revoked_by_advocates(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, _, adv, cid = await _firm(api, database)
    email = _email("client")
    inv = (await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": email}, headers=adv)).json()
    listed = (await api.get(f"/v1/firms/{firm}/clients/{cid}/invitations", headers=adv)).json()["items"]
    assert [i["email"] for i in listed] == [email]
    first = token_from(outbox, email)
    assert (await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations/{inv['id']}/resend", headers=adv)).status_code == 200
    assert token_from(outbox, email) != first  # fresh link; the old one no longer works
    other = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Other"}, headers=adv)).json()["id"]
    assert (await api.delete(f"/v1/firms/{firm}/clients/{other}/invitations/{inv['id']}", headers=adv)).status_code == 404  # wrong client
    assert (await api.delete(f"/v1/firms/{firm}/clients/{cid}/invitations/{inv['id']}", headers=adv)).status_code == 204
    assert (await api.get(f"/v1/firms/{firm}/clients/{cid}/invitations", headers=adv)).json()["items"] == []


async def test_people_without_rights_cannot_revoke(api, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm, owner, adv, _ = await _firm(api, database)
    inv = (await api.post(f"/v1/firms/{firm}/invitations", json={"email": _email("x"), "role": "paralegal"}, headers=owner)).json()
    assert (await api.delete(f"/v1/firms/{firm}/invitations/{inv['id']}", headers=adv)).status_code == 403
    other_firm = new_firm(database)
    _, stranger, _ = await firm_member(api, database, other_firm, "firm_owner")
    assert (await api.delete(f"/v1/firms/{other_firm}/invitations/{inv['id']}", headers=stranger)).status_code == 404  # RLS
