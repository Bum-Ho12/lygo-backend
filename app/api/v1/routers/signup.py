"""Self-service firm sign-up (applicant side) and the platform review queue."""
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import Claims, Factory, client_ip
from app.api.v1.routers.platform import Platform
from app.core.config import settings
from app.core.errors import ProblemError
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.applications import FirmApplication
from app.db.models.tenancy import User
from app.domain.firms import applications as svc
from app.integrations.email.base import EmailSender, get_email_sender
from app.integrations.sms.phone import InvalidPhone, normalize_ke

router = APIRouter(tags=["firm sign-up"])
Email = Annotated[EmailSender, Depends(get_email_sender)]
PracticeArea = Literal[
    "general", "insurance", "immigration", "conveyancing", "employment", "family", "commercial", "litigation",
    "criminal", "tax", "intellectual_property", "banking", "probate", "other",
]


class ApplicationIn(BaseModel):
    firm_name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=3, max_length=63, description="Workspace name: lowercase letters, digits and hyphens.")
    practice_areas: list[PracticeArea] = Field(default_factory=list, max_length=14)
    lsk_admission_no: str | None = Field(default=None, max_length=40, description="Law Society of Kenya admission number (helps us verify the firm).")
    phone: str | None = Field(default=None, max_length=20)
    town: str | None = Field(default=None, max_length=80)
    size: Literal["solo", "2-5", "6-20", "21+"] = "solo"
    accept_terms: bool = Field(description="Accepts the Lygo terms and data processing agreement (see GET /firm-signup).")

    @field_validator("slug")
    @classmethod
    def lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("phone")
    @classmethod
    def e164(cls, v: str | None) -> str | None:
        if not v or not v.strip():
            return None
        try:
            return normalize_ke(v)
        except InvalidPhone as exc:
            raise ValueError("Enter a Kenyan mobile number, e.g. 0712 345 678.") from exc


class ApplicationOut(BaseModel):
    id: uuid.UUID
    firm_name: str
    slug: str
    practice_areas: list[str]
    lsk_admission_no: str | None
    town: str | None
    size: str
    status: str
    review_note: str
    firm_id: uuid.UUID | None
    created_at: datetime
    reviewed_at: datetime | None


class ReviewItem(ApplicationOut):
    applicant_email: str
    applicant_name: str
    phone: str | None
    terms_version: str


def _out(a: FirmApplication) -> ApplicationOut:
    return ApplicationOut(
        id=a.id, firm_name=a.firm_name, slug=a.slug, practice_areas=list(a.practice_areas), lsk_admission_no=a.lsk_admission_no, town=a.town,
        size=a.size, status=a.status, review_note=a.review_note if a.status == "rejected" else "", firm_id=a.firm_id, created_at=a.created_at,
        reviewed_at=a.reviewed_at,
    )


# --- applicant side ------------------------------------------------------------------------------------------------------


@router.get("/firm-signup")
async def signup_info() -> dict[str, str]:
    """Public: whether sign-up is open, and the terms version an applicant accepts (the frontend shows the matching text)."""
    return {"mode": settings.firm_signup_mode, "terms_version": settings.terms_version}


@router.get("/firm-signup/slug-availability")
async def slug_availability(claims: Claims, factory: Factory, slug: Annotated[str, Query(max_length=63)]) -> dict[str, str | bool]:
    """Signed-in only, so the namespace of firms cannot be enumerated anonymously."""
    slug = slug.strip().lower()
    if problem := svc.slug_problem(slug):
        return {"slug": slug, "available": False, "reason": problem}
    ok = await svc._slug_is_free(factory, slug)
    return {"slug": slug, "available": ok, "reason": "Available." if ok else "That name is already taken."}


@router.post("/me/firm-applications", status_code=201)
async def apply(body: ApplicationIn, request: Request, claims: Claims, factory: Factory, email: Email) -> ApplicationOut:
    a = await svc.submit(
        factory, email, claims.user_id, client_ip(request), firm_name=body.firm_name, slug=body.slug, practice_areas=list(body.practice_areas),
        lsk_admission_no=(body.lsk_admission_no or "").strip() or None, phone=body.phone, town=(body.town or "").strip() or None, size=body.size,
        accept_terms=body.accept_terms,
    )
    return _out(a)


@router.get("/me/firm-applications")
async def my_applications(claims: Claims, factory: Factory) -> list[ApplicationOut]:
    async with factory() as db:
        rows = (await db.execute(
            select(FirmApplication).where(FirmApplication.applicant_id == claims.user_id).order_by(FirmApplication.created_at.desc())
        )).scalars().all()
    return [_out(a) for a in rows]


@router.post("/me/firm-applications/{application_id}:withdraw")
async def withdraw(application_id: uuid.UUID, claims: Claims, factory: Factory) -> ApplicationOut:
    return _out(await svc.withdraw(factory, claims.user_id, application_id))


# --- platform review ------------------------------------------------------------------------------------------------------


class ApproveIn(BaseModel):
    slug: str | None = Field(default=None, max_length=63, description="Override the requested workspace name (e.g. if it was taken).")
    note: str = Field(default="", max_length=2000, description="Internal note (e.g. how the firm was verified).")


class RejectIn(BaseModel):
    reason: str = Field(min_length=5, max_length=2000, description="Shown to the applicant.")


@router.get("/platform/firm-applications")
async def review_queue(
    ctx: Platform, factory: Factory, status: Literal["pending", "approved", "rejected", "withdrawn"] = "pending",
) -> list[ReviewItem]:
    async with tenant_session(factory, ctx) as db:
        rows = (await db.execute(
            select(FirmApplication, User.email, User.name).join(User, User.id == FirmApplication.applicant_id)
            .where(FirmApplication.status == status).order_by(FirmApplication.created_at).limit(200)
        )).all()
    return [
        ReviewItem(**_out(a).model_dump() | {"review_note": a.review_note}, applicant_email=em, applicant_name=nm, phone=a.phone, terms_version=a.terms_version)
        for a, em, nm in rows
    ]


@router.post("/platform/firm-applications/{application_id}:approve")
async def approve(application_id: uuid.UUID, body: ApproveIn, ctx: Platform, factory: Factory, email: Email) -> ApplicationOut:
    _require_admin(ctx)
    return _out(await svc.approve(factory, email, ctx, application_id, slug=(body.slug or "").strip().lower() or None, note=body.note))


@router.post("/platform/firm-applications/{application_id}:reject")
async def reject(application_id: uuid.UUID, body: RejectIn, ctx: Platform, factory: Factory, email: Email) -> ApplicationOut:
    _require_admin(ctx)
    return _out(await svc.reject(factory, email, ctx, application_id, reason=body.reason))


def _require_admin(ctx: TenantContext) -> None:
    if ctx.role != "platform_admin":  # support staff can view the queue; only admins decide
        raise ProblemError(403, "forbidden", "Forbidden", "Only a platform admin can decide firm applications.")
