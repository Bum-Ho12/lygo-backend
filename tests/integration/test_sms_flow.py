"""Notification lifecycle against Postgres with a scripted provider (no network, no real SMS)."""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.errors import ProblemError
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.notifications import Notification
from app.domain.notifications import service
from app.integrations.sms.base import SmsAuthError, SmsResult, SmsTransientError, SmsUncertain
from tests.conftest import PgConfig
from tests.integration.helpers import firm_member, new_firm, owner_sql

pytestmark = pytest.mark.asyncio
PHONE = "0712345678"


class ScriptedProvider:
    def __init__(self, *outcomes) -> None:  # type: ignore[no-untyped-def]
        self.outcomes, self.calls = list(outcomes), []

    async def send(self, to, text, *, sender_id=None, callback_url=None):  # type: ignore[no-untyped-def]
        self.calls.append({"to": to, "text": text, "callback_url": callback_url, "sender_id": sender_id})
        await asyncio.sleep(0)
        out = self.outcomes.pop(0) if self.outcomes else "ok"
        if isinstance(out, Exception):
            raise out
        return SmsResult(provider_message_id=str(2000 + len(self.calls)), status="queued", recipients=1)


async def queue(factory, firm, template="update_nudge", params=None, now=None, to=PHONE):  # type: ignore[no-untyped-def]
    ctx = TenantContext(firm_id=firm, user_id=uuid.uuid4(), role="advocate")
    async with tenant_session(factory, ctx) as db:
        n = await service.queue_sms(db, ctx, to=to, template_id=template, params=params or {"firm": "Test Firm"}, now=now)
        return n.id


async def row(factory, firm, nid) -> Notification:  # type: ignore[no-untyped-def]
    async with tenant_session(factory, TenantContext(firm_id=firm, user_id=uuid.uuid4(), role="advocate")) as db:
        n = await db.get(Notification, nid)
        assert n is not None
        return n


# Quiet hours depend on wall-clock time, so tests pin 'now' to midday EAT (09:00 UTC) unless testing them.
NOON = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
NIGHT = datetime(2026, 11, 9, 19, 30, tzinfo=UTC)  # 22:30 EAT


async def test_send_success_keeps_number_until_delivery_then_scrubs(factory, database: PgConfig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "api_base_url", "https://api.lygo.test")
    firm = new_firm(database)
    nid = await queue(factory, firm, now=NOON)
    p = ScriptedProvider()
    assert await service.deliver(factory, p, firm, nid, now=lambda: NOON) == "sent"
    call = p.calls[0]
    assert call["to"] == "+254712345678" and call["text"] == "Lygo: Test Firm has an update for you. Log in to view it."
    assert call["callback_url"] == f"https://api.lygo.test/v1/integrations/thrivewave/delivery-reports/{service.callback_token(nid)}"
    n = await row(factory, firm, nid)
    assert n.status == "sent" and n.provider_txn_id == "2001" and n.recipient == "+254712345678" and n.to_masked == "+25471***78"
    assert await service.deliver(factory, p, firm, nid, now=lambda: NOON) == "sent" and len(p.calls) == 1  # idempotent


async def test_no_callback_url_when_public_url_unset(factory, database: PgConfig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "api_base_url", "")
    firm = new_firm(database)
    nid = await queue(factory, firm, now=NOON)
    p = ScriptedProvider()
    await service.deliver(factory, p, firm, nid, now=lambda: NOON)
    assert p.calls[0]["callback_url"] is None


async def test_transient_errors_retry_with_backoff_then_succeed(factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    nid = await queue(factory, firm, now=NOON)
    p, slept = ScriptedProvider(SmsTransientError("busy"), SmsTransientError("busy")), []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    assert await service.deliver(factory, p, firm, nid, now=lambda: NOON, sleep=fake_sleep) == "sent"
    assert slept == [1, 2] and len(p.calls) == 3 and (await row(factory, firm, nid)).attempts == 3


async def test_transient_errors_exhaust_to_failed_and_scrub(factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    nid = await queue(factory, firm, now=NOON)
    p = ScriptedProvider(*[SmsTransientError("down")] * 5)
    assert await service.deliver(factory, p, firm, nid, now=lambda: NOON, sleep=lambda s: asyncio.sleep(0)) == "failed"
    n = await row(factory, firm, nid)
    assert len(p.calls) == 3 and n.recipient is None and n.rendered_text is None and "down" in n.error


async def test_uncertain_outcome_is_never_retried(factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    nid = await queue(factory, firm, now=NOON)
    p = ScriptedProvider(SmsUncertain("timeout"))
    assert await service.deliver(factory, p, firm, nid, now=lambda: NOON) == "uncertain"
    assert await service.deliver(factory, p, firm, nid, now=lambda: NOON) == "uncertain"
    assert len(p.calls) == 1  # a second send could duplicate the message
    assert (await row(factory, firm, nid)).recipient is None


async def test_auth_failure_fails_fast_with_clear_reason(factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    nid = await queue(factory, firm, now=NOON)
    p = ScriptedProvider(SmsAuthError("expired"))
    assert await service.deliver(factory, p, firm, nid, now=lambda: NOON) == "failed"
    n = await row(factory, firm, nid)
    assert len(p.calls) == 1 and "SmsAuthError" in n.error and n.rendered_text is None


async def test_concurrent_workers_send_once(factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    nid = await queue(factory, firm, now=NOON)
    p = ScriptedProvider()
    results = await asyncio.gather(*[service.deliver(factory, p, firm, nid, now=lambda: NOON) for _ in range(5)])
    assert len(p.calls) == 1 and "sent" in results


async def test_quiet_hours_hold_non_urgent_but_not_otp(factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    held = await queue(factory, firm, now=NIGHT)
    n = await row(factory, firm, held)
    assert n.deliver_after == datetime(2026, 11, 10, 4, 0, tzinfo=UTC)  # 07:00 EAT next morning
    p = ScriptedProvider()
    assert await service.deliver(factory, p, firm, held, now=lambda: NIGHT) == "queued" and p.calls == []
    otp = await queue(factory, firm, template="otp", params={"code": "482913", "minutes": "5"}, now=NIGHT)
    assert await service.deliver(factory, p, firm, otp, now=lambda: NIGHT) == "sent" and "482913" in p.calls[0]["text"]
    morning = datetime(2026, 11, 10, 4, 1, tzinfo=UTC)
    assert await service.deliver(factory, p, firm, held, now=lambda: morning) == "sent"
    assert service.next_allowed_time(datetime(2026, 11, 9, 3, 0, tzinfo=UTC), False) == datetime(2026, 11, 9, 4, 0, tzinfo=UTC)  # 06:00 EAT -> 07:00


async def test_sweeper_sends_due_notifications_across_firms(factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    f1, f2 = new_firm(database), new_firm(database)
    n1, n2 = await queue(factory, f1, now=NOON), await queue(factory, f2, now=NOON)
    owner_sql(database, "UPDATE notifications SET deliver_after = now() - interval '1 minute' WHERE id IN (%s, %s)", n1, n2)
    p = ScriptedProvider()
    assert await service.send_due(factory, p) >= 2
    assert (await row(factory, f1, n1)).status == "sent" and (await row(factory, f2, n2)).status == "sent"


async def test_validation_and_quota(factory, database: PgConfig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    for kwargs in ({"to": "not-a-phone"}, {"template": "nope"}, {"params": {"firm": "Case: X v Y denied"}}):
        with pytest.raises(ProblemError) as e:
            await queue(factory, firm, now=NOON, **kwargs)
        assert e.value.status == 422
    monkeypatch.setattr(settings, "sms_monthly_quota", 2)
    await queue(factory, firm, now=NOON)
    await queue(factory, firm, now=NOON)
    with pytest.raises(ProblemError) as e:
        await queue(factory, firm, now=NOON)
    assert e.value.status == 429
    other = new_firm(database)  # quotas are per firm
    await queue(factory, other, now=NOON)


# --- delivery reports over HTTP ----------------------------------------------------------------------
async def sent(api_factory, firm, now=NOON):  # type: ignore[no-untyped-def]
    nid = await queue(api_factory, firm, now=now)
    p = ScriptedProvider()
    await service.deliver(api_factory, p, firm, nid, now=lambda: now)
    return nid, service.callback_token(nid)


def dlr(msisdn="254712345678", status=3, txn=2001, **extra):  # type: ignore[no-untyped-def]
    return {"msisdn": msisdn, "status": status, "transaction_id": txn, "country": "ke", "network": "Safaricom", "status_description": "Delivered To Teminal", **extra}


async def test_delivery_report_marks_delivered_and_scrubs(api, factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    nid, token = await sent(factory, firm)
    url = f"/v1/integrations/thrivewave/delivery-reports/{token}"
    r = await api.post(url, json=dlr())
    assert r.status_code == 200 and r.json() == {"result": "applied"}
    n = await row(factory, firm, nid)
    assert n.status == "delivered" and n.delivered_at and n.recipient is None and n.rendered_text is None
    assert (await api.post(url, json=dlr(status=0))).json() == {"result": "ignored"}  # terminal: cannot be flipped afterwards
    assert (await row(factory, firm, nid)).status == "delivered"


async def test_delivery_report_must_match_what_was_sent(api, factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    nid, token = await sent(factory, firm)
    url = f"/v1/integrations/thrivewave/delivery-reports/{token}"
    assert (await api.post(url, json=dlr(txn=9999))).json() == {"result": "ignored"}  # wrong transaction id
    assert (await api.post(url, json=dlr(msisdn="254799999999"))).json() == {"result": "ignored"}  # wrong number
    assert (await api.post(url, json=dlr(status=2))).json() == {"result": "ignored"}  # pending: no change
    assert (await row(factory, firm, nid)).status == "sent"
    assert (await api.post(url, json=dlr(msisdn="+254 712 345 678"))).json() == {"result": "applied"}  # number format tolerance


async def test_failed_delivery_report_records_reason(api, factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    nid, token = await sent(factory, firm)
    r = await api.post(f"/v1/integrations/thrivewave/delivery-reports/{token}", json=dlr(status=1, status_description="Absent subscriber"))
    assert r.json() == {"result": "applied"}
    n = await row(factory, firm, nid)
    assert n.status == "failed" and n.error == "Absent subscriber" and n.recipient is None


async def test_delivery_report_endpoint_rejects_forgeries_and_junk(api, factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    firm = new_firm(database)
    nid, token = await sent(factory, firm)
    base = "/v1/integrations/thrivewave/delivery-reports/"
    assert (await api.post(base + "0" * 64, json=dlr())).status_code == 404  # right shape, wrong secret
    assert (await api.post(base + "short", json=dlr())).status_code == 404
    other_firm_token = service.callback_token(uuid.uuid4())
    assert (await api.post(base + other_firm_token, json=dlr())).status_code == 404
    assert (await api.post(base + token, json={"nonsense": True})).status_code == 400
    assert (await api.post(base + token, content=b"not json", headers={"content-type": "application/json"})).status_code == 400
    assert (await api.post(base + token, content=b"x" * 9000)).status_code == 413
    assert (await row(factory, firm, nid)).status == "sent"  # none of that changed anything


async def test_notification_listing_is_masked_and_firm_scoped(api, factory, database: PgConfig) -> None:  # type: ignore[no-untyped-def]
    f1, f2 = new_firm(database), new_firm(database)
    _, admin, _ = await firm_member(api, database, f1, "firm_admin")
    _, para, _ = await firm_member(api, database, f1, "paralegal")
    _, other, _ = await firm_member(api, database, f2, "firm_admin")
    await queue(factory, f1, now=NOON)
    await queue(factory, f2, now=NOON)
    items = (await api.get(f"/v1/firms/{f1}/notifications", headers=admin)).json()["items"]
    assert len(items) == 1 and items[0]["to_masked"] == "+25471***78" and "recipient" not in items[0] and "0712345678" not in str(items)
    assert (await api.get(f"/v1/firms/{f1}/notifications", headers=para)).status_code == 403
    assert len((await api.get(f"/v1/firms/{f2}/notifications", headers=other)).json()["items"]) == 1
    assert (await api.get(f"/v1/firms/{f1}/notifications", headers=other)).status_code == 404
    assert timedelta(0) == timedelta(0)
