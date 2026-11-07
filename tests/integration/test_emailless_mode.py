"""With no email channel (SMTP not configured) the product must still be fully usable and no weaker."""
import re

import pyotp
import pytest

from app.core.config import settings
from app.integrations.email.base import ConsoleEmailSender, get_email_sender
from app.main import app
from tests.conftest import PgConfig
from tests.integration.helpers import PASSWORD, firm_member, make_user, new_firm, owner_sql

pytestmark = pytest.mark.asyncio


@pytest.fixture
def no_email(api):  # type: ignore[no-untyped-def]
    app.dependency_overrides[get_email_sender] = lambda: ConsoleEmailSender()  # nothing is delivered


def token_of(link: str) -> str:
    return re.search(r"[?&]token=([A-Za-z0-9_\-]+)", link).group(1)  # type: ignore[union-attr]


async def test_there_is_no_email_verification_state_any_more(database: PgConfig) -> None:
    cols = {r[0] for r in owner_sql(database, "SELECT column_name FROM information_schema.columns WHERE table_name='users'")}
    assert "email_verified_at" not in cols and "phone_verified_at" in cols


async def test_member_invitation_link_is_returned_to_the_inviter(api, database: PgConfig, no_email, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "frontend_base_url", "https://app.lygo.example")
    firm = new_firm(database)
    _, owner, _ = await firm_member(api, database, firm, "firm_owner")
    _, para, _ = await firm_member(api, database, firm, "paralegal")
    _, invitee, email = await make_user(api, "invitee")
    r = await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)
    assert r.status_code == 201 and r.json()["email_sent"] is False
    link = r.json()["invite_link"]
    assert link.startswith("https://app.lygo.example/accept-invite?token=")
    # the link never leaks through listing, and only people allowed to invite can create one
    assert "token" not in (await api.get(f"/v1/firms/{firm}/invitations", headers=owner)).text
    assert (await api.post(f"/v1/firms/{firm}/invitations", json={"email": "x@example.com", "role": "advocate"}, headers=para)).status_code == 403
    # the invitee accepts with the shared link; the email binding still protects against use by someone else
    _, thief, _ = await make_user(api, "thief")
    assert (await api.post("/v1/invitations/accept", json={"token": token_of(link)}, headers=thief)).status_code == 400
    assert (await api.post("/v1/invitations/accept", json={"token": token_of(link)}, headers=invitee)).status_code == 200
    assert (await api.get(f"/v1/firms/{firm}", headers=invitee)).status_code == 200


async def test_resend_issues_a_new_link_and_kills_the_old_one(api, database: PgConfig, no_email) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, owner, _ = await firm_member(api, database, firm, "firm_owner")
    _, invitee, email = await make_user(api, "invitee")
    first = (await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)).json()
    second = (await api.post(f"/v1/firms/{firm}/invitations/{first['id']}/resend", headers=owner)).json()
    assert second["invite_link"] and second["invite_link"] != first["invite_link"]
    assert (await api.post("/v1/invitations/accept", json={"token": token_of(first["invite_link"])}, headers=invitee)).status_code == 400
    assert (await api.post("/v1/invitations/accept", json={"token": token_of(second["invite_link"])}, headers=invitee)).status_code == 200


async def test_client_portal_and_platform_bootstrap_also_return_links(api, database: PgConfig, no_email) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, adv, _ = await firm_member(api, database, firm, "advocate")
    cid = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Acme"}, headers=adv)).json()["id"]
    _, portal, pemail = await make_user(api, "portal")
    r = await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": pemail}, headers=adv)
    assert r.json()["email_sent"] is False and "invite_link" in r.json()
    assert (await api.post("/v1/invitations/accept", json={"token": token_of(r.json()["invite_link"])}, headers=portal)).status_code == 200

    pid, ph, _ = await make_user(api, "platform")
    owner_sql(database, "UPDATE users SET platform_role='platform_admin' WHERE id=%s", pid)
    _, oh, oemail = await make_user(api, "owner")
    slug = "emailless-firm-1"
    boot = await api.post("/v1/platform/firms", json={"slug": slug, "name": "No Email LLP", "owner_email": oemail}, headers=ph)
    assert boot.status_code == 201 and boot.json()["owner_invitation_emailed"] is False
    assert (await api.post("/v1/invitations/accept", json={"token": token_of(boot.json()["owner_invite_link"])}, headers=oh)).status_code == 200
    assert (await api.get(f"/v1/firms/{boot.json()['id']}", headers=oh)).json()["name"] == "No Email LLP"


async def test_delivered_invitations_never_expose_the_link(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, owner, _ = await firm_member(api, database, firm, "firm_owner")
    r = await api.post(f"/v1/firms/{firm}/invitations", json={"email": "someone@example.com", "role": "advocate"}, headers=owner)
    assert r.json()["email_sent"] is True and r.json()["invite_link"] is None and outbox.sent  # the secret travels only by email


async def test_forgot_password_has_nothing_to_send_and_creates_nothing(api, database: PgConfig, no_email) -> None:  # type: ignore[no-untyped-def]
    uid, _, email = await make_user(api, "forgetful")
    r = await api.post("/v1/auth/password/forgot", json={"email": email})
    assert r.status_code == 202 and "reset link" in r.json()["status"]  # same answer as ever: no enumeration
    assert owner_sql(database, "SELECT count(*) FROM password_reset_tokens WHERE user_id=%s", uid)[0][0] == 0  # no dangling tokens


async def test_platform_reset_link_recovery_is_audited_and_does_not_bypass_mfa(api, database: PgConfig, no_email, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pid, ph, _ = await make_user(api, "ops")
    owner_sql(database, "UPDATE users SET platform_role='platform_admin' WHERE id=%s", pid)
    uid, uh, email = await make_user(api, "locked-out")
    secret = (await api.post("/v1/me/mfa/totp/enroll", headers=uh)).json()["secret"]
    import time

    await api.post("/v1/me/mfa/totp/confirm", json={"code": pyotp.TOTP(secret).at(time.time() - 30)}, headers=uh)

    assert (await api.post("/v1/platform/users/reset-link", json={"email": email}, headers=uh)).status_code == 403  # ordinary users cannot
    assert (await api.post("/v1/platform/users/reset-link", json={"email": "ghost@example.com"}, headers=ph)).status_code == 404
    r = await api.post("/v1/platform/users/reset-link", json={"email": email.upper()}, headers=ph)
    assert r.status_code == 200 and r.json()["expires_in_minutes"] == settings.password_reset_ttl_minutes
    new = "operator assisted passphrase 3"
    assert (await api.post("/v1/auth/password/reset", json={"token": token_of(r.json()["reset_link"]), "new_password": new})).status_code == 204
    assert (await api.post("/v1/auth/password/reset", json={"token": token_of(r.json()["reset_link"]), "new_password": new + "!"})).status_code == 400  # single use
    step = (await api.post("/v1/auth/login", json={"email": email, "password": new})).json()
    assert step["mfa_required"] is True and step["access_token"] is None  # the operator-issued reset did not hand over the account

    events = owner_sql(database, "SELECT target, meta FROM audit_events WHERE action='platform.reset_link' AND actor_id=%s ORDER BY id", pid)
    assert [e[1]["found"] for e in events] == [False, True] and all(email.split("@")[0] not in e[0] for e in events)  # audited, address hashed

    monkeypatch.setattr(settings, "mfa_required_for_firm_roles", True)
    again = await api.post("/v1/platform/users/reset-link", json={"email": email}, headers=ph)
    assert again.status_code == 403 and again.json()["code"] == "mfa_required"  # and operators themselves need a second factor
    assert PASSWORD and uid
