from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import Factory, UserId
from app.core.errors import ProblemError
from app.db.models.tenancy import User
from app.domain.firms import service

router = APIRouter(tags=["invitations"])


class AcceptIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@router.post("/invitations/accept")
async def accept(body: AcceptIn, user_id: UserId, factory: Factory) -> dict[str, str | None]:
    """Authenticated user redeems an invitation. The invitee has no tenant yet; RLS only exposes the
    single invitation whose token hash is presented (policy `invitee_token`)."""
    async with factory() as db, db.begin():
        user = await db.get(User, user_id)
        if user is None:
            raise ProblemError(401, "unauthorized", "Unauthorized", "Unknown user.")
        result = await service.accept_invitation(db, user, body.token)
    return result

