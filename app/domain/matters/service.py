import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ProblemError
from app.db.models.tenancy import FirmMembership


async def validate_assignees(db: AsyncSession, firm_id: uuid.UUID, user_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """Assigned users must be active members of this firm (identity table: explicit firm filter)."""
    wanted = set(user_ids)
    if not wanted:
        return []
    found = set(
        (
            await db.execute(
                select(FirmMembership.user_id).where(
                    FirmMembership.firm_id == firm_id, FirmMembership.status == "active", FirmMembership.user_id.in_(wanted)
                )
            )
        ).scalars()
    )
    if found != wanted:
        raise ProblemError(422, "invalid_assignee", "Invalid assignee", "All assignees must be active members of this firm.")
    return sorted(found, key=str)


async def get_or_404[T](db: AsyncSession, model: type[T], id_: uuid.UUID, what: str) -> T:
    obj = await db.get(model, id_)  # RLS hides rows of other tenants/clients -> None
    if obj is None or getattr(obj, "deleted_at", None) is not None:
        raise NotFound(what)
    return obj
