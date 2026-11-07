"""Invitations depend on email: delivery failures must be visible and recoverable, and links must be safe."""
import pytest

from app.core.config import settings
from app.integrations.email.base import EmailError
from app.integrations.email.base import get_email_sender as dep
from app.main import app
from tests.conftest import PgConfig
from tests.integration.helpers import firm_member, make_user, new_firm, token_from

pytestmark = pytest.mark.asyncio


class DownSender:
    def __init__(self, transient: bool = True) -> None:
        self.transient, self.attempts = transient, 0

    async def send(self, message) -> None:  # type: ignore[no-untyped-def]
        self.attempts += 1
        raise EmailError("smtp unavailable", transient=self.transient)


async def test_failed_delivery_is_reported_and_resend_recovers(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, owner, _ = await firm_member(api, database, firm, "firm_owner")
    _, invitee, email = await make_user(api, "invitee")
    app.dependency_overrides[dep] = lambda: DownSender()
    r = await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)
    assert r.status_code == 201 and r.json()["email_sent"] is False  # created, but the owner is told it was not delivered
    inv_id = r.json()["id"]
    pending = (await api.get(f"/v1/firms/{firm}/invitations", headers=owner)).json()["items"]
    assert [(i["id"], i["email"], i["expired"]) for i in pending] == [(inv_id, email, False)]

    app.dependency_overrides[dep] = lambda: outbox  # email fixed
    ok = await api.post(f"/v1/firms/{firm}/invitations/{inv_id}/resend", headers=owner)
    assert ok.status_code == 200 and ok.json()["email_sent"] is True
    token = token_from(outbox, email)
    acc = await api.post("/v1/invitations/accept", json={"token": token}, headers=invitee)
    assert acc.status_code == 200 and acc.json()["role"] == "advocate"
    assert (await api.get(f"/v1/firms/{firm}/invitations", headers=owner)).json()["items"] == []  # accepted ones drop off the pending list
    assert (await api.post(f"/v1/firms/{firm}/invitations/{inv_id}/resend", headers=owner)).status_code == 404  # nothing to resend


async def test_resend_invalidates_the_previous_link(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, owner, _ = await firm_member(api, database, firm, "firm_owner")
    _, invitee, email = await make_user(api, "invitee")
    inv = (await api.post(f"/v1/firms/{firm}/invitations", json={"email": email, "role": "advocate"}, headers=owner)).json()
    first = token_from(outbox, email)
    await api.post(f"/v1/firms/{firm}/invitations/{inv['id']}/resend", headers=owner)
    second = token_from(outbox, email)
    assert first != second
    assert (await api.post("/v1/invitations/accept", json={"token": first}, headers=invitee)).status_code == 400  # a leaked old link is dead
    assert (await api.post("/v1/invitations/accept", json={"token": second}, headers=invitee)).status_code == 200


async def test_resend_and_listing_are_permissioned_and_firm_scoped(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm, other = new_firm(database), new_firm(database)
    _, owner, _ = await firm_member(api, database, firm, "firm_owner")
    _, para, _ = await firm_member(api, database, firm, "paralegal")
    _, other_owner, _ = await firm_member(api, database, other, "firm_owner")
    inv = (await api.post(f"/v1/firms/{firm}/invitations", json={"email": "someone@example.com", "role": "advocate"}, headers=owner)).json()
    assert (await api.post(f"/v1/firms/{firm}/invitations/{inv['id']}/resend", headers=para)).status_code == 403
    assert (await api.get(f"/v1/firms/{firm}/invitations", headers=para)).status_code == 403
    assert (await api.post(f"/v1/firms/{other}/invitations/{inv['id']}/resend", headers=other_owner)).status_code == 404  # another firm's invitation
    assert (await api.get(f"/v1/firms/{other}/invitations", headers=other_owner)).json()["items"] == []


async def test_invitation_email_contents_are_safe(api, database: PgConfig, outbox, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "frontend_base_url", "https://app.lygo.example")
    firm = new_firm(database)
    from tests.integration.helpers import owner_sql

    owner_sql(database, "UPDATE firms SET name = %s WHERE id = %s", "<b>Evil</b>\r\nBcc: x@y.z Associates", firm)
    _, owner, _ = await firm_member(api, database, firm, "firm_owner")
    await api.post(f"/v1/firms/{firm}/invitations", json={"email": "a@example.com", "role": "advocate"}, headers=owner)
    msg = outbox.sent[-1]
    assert msg.to == "a@example.com" and msg.tags == {"template": "invitation"}
    assert "https://app.lygo.example/accept-invite?token=" in msg.body
    assert "<b>" not in msg.subject and "\n" not in msg.subject and "\r" not in msg.subject  # firm name is sanitised before use
    assert "<b>" not in (msg.html or "")
    assert "Associates" in msg.subject  # but a legitimate name survives


async def test_client_portal_invitations_report_delivery_too(api, database: PgConfig, outbox) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    _, adv, _ = await firm_member(api, database, firm, "advocate")
    cid = (await api.post(f"/v1/firms/{firm}/clients", json={"name": "Acme"}, headers=adv)).json()["id"]
    app.dependency_overrides[dep] = lambda: DownSender(transient=False)
    r = await api.post(f"/v1/firms/{firm}/clients/{cid}/invitations", json={"email": "c@example.com"}, headers=adv)
    assert r.status_code == 201 and r.json()["email_sent"] is False
