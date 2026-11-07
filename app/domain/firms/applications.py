"""Self-service firm sign-up: a registered user applies; a platform admin approves (or it is approved instantly in 'auto' mode).

On approval the firm is created and the applicant becomes its owner in ONE transaction. No invitation is needed: the
application is bound to the applicant's authenticated account.
"""
import re
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import Forbidden, NotFound, ProblemError
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.applications import FirmApplication
from app.db.models.tenancy import Firm, FirmMembership, User
from app.domain.audit import service as audit
from app.domain.firms.service import create_firm
from app.domain.users import throttle
from app.integrations.email.base import EmailError, EmailSender
from app.integrations.email.templates import EmailTemplateError, link, render

log = structlog.get_logger()
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
# names that would collide with routes, subdomains or impersonate Lygo/regulators
RESERVED_SLUGS = frozenset({
    "admin", "api", "app", "www", "mail", "email", "smtp", "support", "help", "status", "docs", "blog", "static", "assets", "cdn",
    "lygo", "platform", "system", "root", "auth", "login", "signup", "register", "billing", "security", "test", "demo",
    "lsk", "odpc", "judiciary", "kenyalaw", "government", "gov",
})
APPLICATIONS_PER_DAY = 3


def slug_problem(slug: str) -> str | None:
    if not SLUG.match(slug):
        return "Use 3-63 lowercase letters, digits and hyphens; start and end with a letter or digit."
    if slug in RESERVED_SLUGS or slug.startswith(("lygo-", "xn--")) or "--" in slug:
        return "That name is reserved."
    return None


async def slug_available(db: AsyncSession, slug: str) -> tuple[bool, str]:
    if problem := slug_problem(slug):
        return False, problem
    taken = (await db.execute(select(func.count()).select_from(Firm).where(Firm.slug == slug))).scalar_one()
    pending = (await db.execute(select(func.count()).select_from(FirmApplication).where(FirmApplication.slug == slug, FirmApplication.status == "pending"))).scalar_one()
    return (False, "That name is already taken.") if (taken or pending) else (True, "Available.")


async def _slug_is_free(factory: async_sessionmaker[AsyncSession], slug: str) -> bool:
    """Firms are RLS-protected, so existence is checked as the platform (which may read firm metadata)."""
    ctx = TenantContext(firm_id=None, user_id=uuid.UUID(int=0), role="platform_support")
    async with tenant_session(factory, ctx) as db:
        ok, _ = await slug_available(db, slug)
        return ok


async def submit(
    factory: async_sessionmaker[AsyncSession], email: EmailSender, user_id: uuid.UUID, ip: str | None, *, firm_name: str, slug: str,
    practice_areas: list[str], lsk_admission_no: str | None, phone: str | None, town: str | None, size: str, accept_terms: bool,
) -> FirmApplication:
    if settings.firm_signup_mode == "closed":
        raise Forbidden("New firm sign-ups are currently closed. Contact Lygo support.")
    if not accept_terms:
        raise ProblemError(422, "terms_required", "Terms not accepted", f"Accept the terms and data processing agreement ({settings.terms_version}).")
    if problem := slug_problem(slug):
        raise ProblemError(422, "invalid_slug", "Invalid workspace name", problem)
    if not await _slug_is_free(factory, slug):
        raise ProblemError(409, "slug_taken", "Name taken", "That workspace name is already taken.")
    async with factory() as db:
        ku, ki = throttle.key("firm-apply-user", str(user_id)), throttle.key("firm-apply-ip", ip or "unknown")
        if secs := await throttle.remaining(db, ku, ki):
            raise throttle.too_many(secs)
        await throttle.hit(db, ku, limit=APPLICATIONS_PER_DAY, window=86_400, lock=86_400)
        await throttle.hit(db, ki, limit=10, window=86_400, lock=86_400)
        await db.commit()  # count the attempt even if the insert below fails
        app_row = FirmApplication(
            applicant_id=user_id, firm_name=" ".join(firm_name.split()), slug=slug, practice_areas=practice_areas, lsk_admission_no=lsk_admission_no,
            phone=phone, town=town, size=size, terms_version=settings.terms_version, terms_accepted_at=datetime.now(UTC),
        )
        db.add(app_row)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            mine = (await db.execute(select(func.count()).select_from(FirmApplication).where(FirmApplication.applicant_id == user_id, FirmApplication.status == "pending"))).scalar_one()
            if mine:
                raise ProblemError(409, "application_pending", "Application already pending", "You already have an application under review.") from exc
            raise ProblemError(409, "slug_taken", "Name taken", "That workspace name is already taken.") from exc
        await db.refresh(app_row)
    log.info("firm_application.submitted", application=str(app_row.id), mode=settings.firm_signup_mode)
    if settings.firm_signup_mode == "auto":
        system = TenantContext(firm_id=None, user_id=user_id, role="platform_admin")  # auto-approval runs as the platform system
        return await approve(factory, email, system, app_row.id, note="auto-approved (FIRM_SIGNUP_MODE=auto)")
    return app_row


async def approve(
    factory: async_sessionmaker[AsyncSession], email: EmailSender, ctx: TenantContext, application_id: uuid.UUID, *, slug: str | None = None, note: str = "",
) -> FirmApplication:
    async with tenant_session(factory, ctx) as db:
        a = (await db.execute(select(FirmApplication).where(FirmApplication.id == application_id).with_for_update())).scalar_one_or_none()
        if a is None:
            raise NotFound("application")
        if a.status != "pending":
            raise ProblemError(409, "not_pending", "Already decided", f"This application is {a.status}.")
        final_slug = slug or a.slug
        if slug and (problem := slug_problem(slug)):
            raise ProblemError(422, "invalid_slug", "Invalid workspace name", problem)
        if (await db.execute(select(func.count()).select_from(Firm).where(Firm.slug == final_slug))).scalar_one():
            raise ProblemError(409, "slug_taken", "Name taken", "A firm with this workspace name already exists; approve with a different slug.")
        firm = await create_firm(db, ctx, slug=final_slug, name=a.firm_name)
        db.add(FirmMembership(firm_id=firm.id, user_id=a.applicant_id, role="firm_owner"))
        a.status, a.firm_id, a.slug = "approved", firm.id, final_slug
        a.reviewed_by, a.reviewed_at, a.review_note = ctx.user_id, datetime.now(UTC), note.strip()[:2000]
        await audit.record(db, ctx, "firm_application.approve", target=f"application:{a.id}", meta={"firm": str(firm.id)})
        applicant = await db.get(User, a.applicant_id)
        to, firm_name = (applicant.email if applicant else None), a.firm_name
    if to:
        await _notify(email, "firm_approved", to, firm_name)
    return a


async def reject(factory: async_sessionmaker[AsyncSession], email: EmailSender, ctx: TenantContext, application_id: uuid.UUID, *, reason: str) -> FirmApplication:
    if len(reason.strip()) < 5:
        raise ProblemError(422, "reason_required", "Reason required", "Give the applicant a short reason.")
    async with tenant_session(factory, ctx) as db:
        a = (await db.execute(select(FirmApplication).where(FirmApplication.id == application_id).with_for_update())).scalar_one_or_none()
        if a is None:
            raise NotFound("application")
        if a.status != "pending":
            raise ProblemError(409, "not_pending", "Already decided", f"This application is {a.status}.")
        a.status, a.reviewed_by, a.reviewed_at, a.review_note = "rejected", ctx.user_id, datetime.now(UTC), reason.strip()[:2000]
        await audit.record(db, ctx, "firm_application.reject", target=f"application:{a.id}")
        applicant = await db.get(User, a.applicant_id)
        to, firm_name = (applicant.email if applicant else None), a.firm_name
    if to:
        await _notify(email, "firm_rejected", to, firm_name)
    return a


async def withdraw(factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID, application_id: uuid.UUID) -> FirmApplication:
    async with factory() as db, db.begin():
        a = (await db.execute(select(FirmApplication).where(FirmApplication.id == application_id, FirmApplication.applicant_id == user_id).with_for_update())).scalar_one_or_none()
        if a is None:
            raise NotFound("application")  # someone else's application looks exactly like a missing one
        if a.status != "pending":
            raise ProblemError(409, "not_pending", "Already decided", f"This application is {a.status}.")
        a.status = "withdrawn"
    return a


async def _notify(email: EmailSender, template: str, to: str, firm_name: str) -> None:
    if not getattr(email, "delivers", True):
        return
    safe = re.sub(r"[^A-Za-z0-9 &.,'\-]", "", firm_name)[:60].strip() or "your firm"
    try:
        await email.send(render(template, to, {"firm": safe, "link": link("")}))
    except (EmailError, EmailTemplateError) as exc:
        log.warning("firm_application.email_failed", reason=str(exc)[:120])
