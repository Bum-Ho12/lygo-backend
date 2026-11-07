"""SMS notifications: queue -> deliver (with safe retry rules) -> delivery report. See docs/10 and THRIVEWAVE.md."""
import asyncio
import hashlib
import hmac
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import ProblemError
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.notifications import Notification
from app.integrations.sms.base import (
    DlrEvent,
    SmsAuthError,
    SmsError,
    SmsProvider,
    SmsRejected,
    SmsTransientError,
    SmsUncertain,
)
from app.integrations.sms.phone import InvalidPhone, mask, normalize_ke, same_number
from app.integrations.sms.templates import TemplateError, render

EAT = ZoneInfo("Africa/Nairobi")
QUIET_START, QUIET_END = time(21, 0), time(7, 0)
MAX_ATTEMPTS = 3
TERMINAL = {"delivered", "failed", "uncertain"}
log = structlog.get_logger()


def callback_token(notification_id: uuid.UUID) -> str:
    """Per-message secret for the unsigned provider callback; derived, so nothing secret is stored."""
    key = hashlib.sha256(f"lygo-dlr:{settings.jwt_secret}".encode()).digest()
    return hmac.new(key, str(notification_id).encode(), hashlib.sha256).hexdigest()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def next_allowed_time(now: datetime, urgent: bool) -> datetime:
    """Non-urgent SMS are held during quiet hours (21:00-07:00 EAT)."""
    if urgent:
        return now
    local = now.astimezone(EAT)
    t = local.time()
    if QUIET_START <= t or t < QUIET_END:
        day = local.date() if t < QUIET_END else local.date() + timedelta(days=1)
        return datetime.combine(day, QUIET_END, tzinfo=EAT).astimezone(UTC)
    return now


async def queue_sms(
    db: AsyncSession, ctx: TenantContext, *, to: str, template_id: str, params: dict[str, str],
    user_id: uuid.UUID | None = None, now: datetime | None = None,
) -> Notification:
    assert ctx.firm_id
    now = now or datetime.now(UTC)
    try:
        number = normalize_ke(to)
        template, body = render(template_id, params)
    except (InvalidPhone, TemplateError) as exc:
        raise ProblemError(422, "invalid_sms", "Invalid SMS", str(exc)) from exc
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = (await db.execute(select(func.count()).select_from(Notification).where(Notification.created_at >= start))).scalar_one()
    quota = settings.sms_monthly_quota
    if used >= quota:
        raise ProblemError(429, "sms_quota_exceeded", "SMS quota exceeded", f"Monthly SMS quota ({quota}) reached.")
    nid = uuid.uuid4()
    n = Notification(
        id=nid, firm_id=ctx.firm_id, user_id=user_id, template=template.id, template_version=template.version,
        urgent=template.urgent, recipient=number, to_masked=mask(number), rendered_text=body,
        token_hash=token_digest(callback_token(nid)), deliver_after=next_allowed_time(now, template.urgent),
    )
    db.add(n)
    await db.flush()
    return n


def _callback_url(nid: uuid.UUID) -> str | None:
    if not settings.api_base_url:
        return None
    return f"{settings.api_base_url.rstrip('/')}/v1/integrations/thrivewave/delivery-reports/{callback_token(nid)}"


async def deliver(
    factory: async_sessionmaker[AsyncSession], provider: SmsProvider, firm_id: uuid.UUID, notification_id: uuid.UUID,
    *, now: Callable[[], datetime] = lambda: datetime.now(UTC), sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Send one notification. Returns its resulting status. Safe to call repeatedly (claims the row first)."""
    ctx = TenantContext(firm_id=firm_id, user_id=uuid.UUID(int=0), role="worker")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        async with tenant_session(factory, ctx) as db:
            # Claim atomically so two workers can never send the same message (the provider has no idempotency key).
            claimed = (
                await db.execute(
                    update(Notification)
                    .where(Notification.id == notification_id, Notification.status == "queued", Notification.deliver_after <= now())
                    .values(status="sending", attempts=Notification.attempts + 1)
                    .returning(Notification.recipient, Notification.rendered_text)
                )
            ).first()
            if claimed is None:
                n = await db.get(Notification, notification_id)
                return n.status if n else "missing"
            recipient, body = claimed
        try:
            assert recipient and body
            result = await provider.send(recipient, body, sender_id=settings.thrivewave_sender_id or None, callback_url=_callback_url(notification_id))
        except SmsTransientError as exc:
            await _update(factory, ctx, notification_id, status="queued" if attempt < MAX_ATTEMPTS else "failed", error=str(exc))
            if attempt < MAX_ATTEMPTS:
                await sleep(2 ** (attempt - 1))
                continue
            await _scrub(factory, ctx, notification_id)
            return "failed"
        except SmsUncertain as exc:
            await _update(factory, ctx, notification_id, status="uncertain", error=str(exc))
            await _scrub(factory, ctx, notification_id)
            log.warning("sms.uncertain", notification_id=str(notification_id))
            return "uncertain"
        except (SmsAuthError, SmsRejected, SmsError) as exc:
            await _update(factory, ctx, notification_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            await _scrub(factory, ctx, notification_id)
            if isinstance(exc, SmsAuthError):
                log.error("sms.auth_failed", detail="Thrivewave API key rejected or expired; rotate THRIVEWAVE_API_KEY")
            return "failed"
        await _update(factory, ctx, notification_id, status="sent", provider_txn_id=result.provider_message_id, sent_at=now())
        return "sent"
    return "failed"  # pragma: no cover


async def _update(factory: async_sessionmaker[AsyncSession], ctx: TenantContext, nid: uuid.UUID, **values: object) -> None:
    async with tenant_session(factory, ctx) as db:
        await db.execute(update(Notification).where(Notification.id == nid).values(**values))


async def _scrub(factory: async_sessionmaker[AsyncSession], ctx: TenantContext, nid: uuid.UUID) -> None:
    """Terminal state: erase the number and message text (data minimisation); keep the masked number."""
    await _update(factory, ctx, nid, recipient=None, rendered_text=None)


async def send_due(factory: async_sessionmaker[AsyncSession], provider: SmsProvider, *, limit: int = 100) -> int:
    """Sweeper for notifications held back by quiet hours (run periodically by Celery beat)."""
    sweeper = TenantContext(firm_id=None, user_id=uuid.UUID(int=0), role="system_sweeper")
    async with tenant_session(factory, sweeper) as db:
        rows = (
            await db.execute(
                select(Notification.id, Notification.firm_id)
                .where(Notification.status == "queued", Notification.deliver_after <= datetime.now(UTC)).limit(limit)
            )
        ).all()
    for nid, fid in rows:
        await deliver(factory, provider, fid, nid)
    return len(rows)


_RANK = {"queued": 0, "sending": 1, "sent": 2, "uncertain": 3, "failed": 4, "delivered": 4}


async def handle_dlr(factory: async_sessionmaker[AsyncSession], token: str, event: DlrEvent) -> str:
    """Apply a provider delivery report. Returns 'applied', 'ignored' or 'unknown'."""
    digest = token_digest(token)
    async with factory() as db, db.begin():
        await db.execute(text("SELECT set_config('app.dlr_token_hash', :h, true)"), {"h": digest})
        n = (await db.execute(select(Notification).where(Notification.token_hash == digest).with_for_update())).scalar_one_or_none()
        if n is None:
            return "unknown"
        if n.status in {"delivered", "failed"}:
            return "ignored"  # already terminal
        # The report must match what we sent: same transaction id and same recipient number.
        if n.provider_txn_id and event.transaction_id != n.provider_txn_id:
            return "ignored"
        if n.recipient and not same_number(n.recipient, event.msisdn):
            return "ignored"
        if event.status == "pending":
            return "ignored"
        n.status = event.status
        n.error = "" if event.status == "delivered" else (event.detail or "delivery failed")
        if event.status == "delivered":
            n.delivered_at = datetime.now(UTC)
        n.recipient, n.rendered_text = None, None
        return "applied"
