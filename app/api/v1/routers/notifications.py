import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Factory, require, tenant_db
from app.core.errors import NotFound, ProblemError
from app.core.pagination import Page, keyset_page
from app.core.tenancy import TenantContext
from app.db.models.notifications import Notification
from app.domain.notifications import service
from app.integrations.sms.thrivewave import parse_dlr

router = APIRouter(tags=["notifications"])
MAX_DLR_BYTES = 8192


class NotificationOut(BaseModel):
    id: uuid.UUID
    template: str
    to_masked: str
    status: str
    error: str
    created_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None


@router.get("/firms/{firm_id}/notifications", response_model=Page[NotificationOut])
async def list_notifications(
    db: Annotated[AsyncSession, Depends(tenant_db)], ctx: Annotated[TenantContext, Depends(require("audit.read"))],
    cursor: str | None = None, limit: int | None = None,
) -> Page[NotificationOut]:
    rows, nxt = await keyset_page(db, select(Notification), Notification.id, cursor=cursor, limit=limit)
    return Page[NotificationOut](
        items=[NotificationOut(id=n.id, template=n.template, to_masked=n.to_masked, status=n.status, error=n.error,
                               created_at=n.created_at, sent_at=n.sent_at, delivered_at=n.delivered_at) for n in rows],
        next_cursor=nxt,
    )


@router.post("/integrations/thrivewave/delivery-reports/{token}")
async def thrivewave_dlr(token: str, request: Request, factory: Factory) -> dict[str, Any]:
    """Provider callback. Unauthenticated by design (the provider cannot sign); the per-message secret in the
    URL, the transaction id and the recipient number are all checked. Unknown tokens get a plain 404."""
    raw = await request.body()
    if len(raw) > MAX_DLR_BYTES:
        raise ProblemError(413, "payload_too_large", "Payload too large", "Delivery report too large.")
    try:
        event = parse_dlr(await request.json())
    except ValueError as exc:
        raise ProblemError(400, "malformed_report", "Malformed report", "Could not parse the delivery report.") from exc
    if len(token) != 64:
        raise NotFound("delivery report target")
    outcome = await service.handle_dlr(factory, token, event)
    if outcome == "unknown":
        raise NotFound("delivery report target")
    return {"result": outcome}
