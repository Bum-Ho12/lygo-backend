import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import insert, select

from app.api.deps import Factory, UserId
from app.core.errors import Forbidden, ProblemError
from app.core.permissions import has_permission
from app.core.security import hash_refresh_token
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.tenancy import Firm, User
from app.db.models.work import Invitation
from app.domain.firms import service
from app.engine.rag.corpus import CorpusManifest, LicenceNotConfirmed, load_corpus_document
from app.engine.rag.embeddings.base import Embedder
from app.engine.rag.embeddings.factory import get_embedder
from app.integrations.email.base import EmailMessage, EmailSender, get_email_sender

router = APIRouter(prefix="/platform", tags=["platform"])


async def platform_ctx(user_id: UserId, factory: Factory) -> TenantContext:
    async with factory() as s:
        user = await s.get(User, user_id)
    if user is None or not user.platform_role or not has_permission(user.platform_role, "platform.read"):
        raise Forbidden("Platform access required.")
    return TenantContext(firm_id=None, user_id=user_id, role=user.platform_role)


Platform = Annotated[TenantContext, Depends(platform_ctx)]


class FirmCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=200)
    owner_email: EmailStr


@router.get("/firms")
async def list_firms(ctx: Platform, factory: Factory) -> dict[str, list[dict[str, str]]]:
    async with tenant_session(factory, ctx) as db:
        rows = (await db.execute(select(Firm).order_by(Firm.created_at))).scalars()
        return {"items": [{"id": str(f.id), "slug": f.slug, "name": f.name, "plan": f.plan} for f in rows]}


@router.post("/firms", status_code=201)
async def create_firm(
    body: FirmCreate, ctx: Platform, factory: Factory, email: Annotated[EmailSender, Depends(get_email_sender)]
) -> dict[str, str]:
    """Create a firm and invite its first owner. Platform staff get no access to firm content."""
    if not has_permission(ctx.role, "platform.manage"):
        raise Forbidden("Only platform admins can create firms.")
    async with tenant_session(factory, ctx) as db:
        firm = await service.create_firm(db, ctx, slug=body.slug, name=body.name)
        # Platform staff may INSERT (never read) invitations. A plain INSERT without RETURNING is required:
        # RETURNING would apply the SELECT policy to the new row, which platform roles intentionally lack.
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        await db.execute(
            insert(Invitation.__table__).values(  # type: ignore[arg-type]
                id=uuid.uuid4(), firm_id=firm.id, email=str(body.owner_email).lower(), role="firm_owner",
                token_hash=hash_refresh_token(token), invited_by=ctx.user_id, created_at=now, updated_at=now,
                expires_at=now + service.INVITE_TTL,
            )
        )
        firm_id = firm.id
    await email.send(EmailMessage(
        to=str(body.owner_email), subject=f"Your Lygo firm '{body.name}' is ready",
        body=f"Accept your owner invitation with this code (valid 7 days): {token}",
    ))
    return {"id": str(firm_id), "slug": body.slug}



class CorpusLoadIn(BaseModel):
    manifest: CorpusManifest
    text: str = Field(min_length=20, max_length=5_000_000)


@router.post("/corpus/documents", status_code=201)
async def load_corpus(
    body: CorpusLoadIn, ctx: Platform, factory: Factory, embedder: Annotated[Embedder | None, Depends(get_embedder)]
) -> dict[str, str | int]:
    """Load one version of a public-law source into the shared corpus (platform admins only)."""
    if not has_permission(ctx.role, "platform.manage"):
        raise Forbidden("Only platform admins can load the corpus.")
    if embedder is None:
        raise ProblemError(503, "search_unavailable", "Search unavailable", "No embedding model is configured.")
    try:
        doc, n = await load_corpus_document(factory, embedder, body.manifest, body.text, actor=ctx.user_id)
    except LicenceNotConfirmed as exc:
        raise ProblemError(422, "licence_not_confirmed", "Licence not confirmed", str(exc)) from exc
    return {"id": str(doc.id), "manifest_id": doc.manifest_id, "chunks": n}
