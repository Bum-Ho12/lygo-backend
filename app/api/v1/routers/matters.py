import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require, tenant_db
from app.core.errors import NotFound
from app.core.pagination import Page, keyset_page
from app.core.tenancy import TenantContext
from app.db.models.tenancy import Client, Matter
from app.db.models.work import Deadline, Task
from app.domain.audit import service as audit
from app.domain.matters.service import get_or_404, validate_assignees

router = APIRouter(prefix="/firms/{firm_id}", tags=["matters"])
Db = Annotated[AsyncSession, Depends(tenant_db)]
Read = Annotated[TenantContext, Depends(require("matters.read"))]
Manage = Annotated[TenantContext, Depends(require("matters.manage"))]
TaskRead = Annotated[TenantContext, Depends(require("tasks.read"))]
TaskManage = Annotated[TenantContext, Depends(require("tasks.manage"))]
Limit = Annotated[int | None, Query(ge=1, le=200)]


class MatterIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    practice_area: str = Field(default="general", max_length=64)
    assigned_user_ids: list[uuid.UUID] = []


class MatterPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    practice_area: str | None = Field(default=None, max_length=64)
    status: Literal["open", "closed", "on_hold"] | None = None
    assigned_user_ids: list[uuid.UUID] | None = None


class MatterOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    title: str
    practice_area: str
    status: str
    assigned_user_ids: list[uuid.UUID]


class DeadlineIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    kind: str = Field(default="general", max_length=32)
    due_at: AwareDatetime


class DeadlinePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    due_at: AwareDatetime | None = None
    status: Literal["open", "done", "dismissed"] | None = None


class DeadlineOut(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID
    title: str
    kind: str
    due_at: datetime
    status: str


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    matter_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    due_at: AwareDatetime | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    status: Literal["open", "in_progress", "done"] | None = None
    assignee_id: uuid.UUID | None = None
    due_at: AwareDatetime | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: str
    status: str
    matter_id: uuid.UUID | None
    client_id: uuid.UUID | None
    assignee_id: uuid.UUID | None
    due_at: datetime | None


def _m(m: Matter) -> MatterOut:
    return MatterOut(id=m.id, client_id=m.client_id, title=m.title, practice_area=m.practice_area, status=m.status,
                     assigned_user_ids=list(m.assigned_user_ids or []))


def _d(d: Deadline) -> DeadlineOut:
    return DeadlineOut(id=d.id, matter_id=d.matter_id, title=d.title, kind=d.kind, due_at=d.due_at, status=d.status)


def _t(t: Task) -> TaskOut:
    return TaskOut(id=t.id, title=t.title, kind=t.kind, status=t.status, matter_id=t.matter_id, client_id=t.client_id,
                   assignee_id=t.assignee_id, due_at=t.due_at)


# --- matters ---------------------------------------------------------------------------------
@router.post("/clients/{client_id}/matters", status_code=201, response_model=MatterOut)
async def create_matter(client_id: uuid.UUID, body: MatterIn, db: Db, ctx: Manage) -> MatterOut:
    assert ctx.firm_id
    await get_or_404(db, Client, client_id, "client")
    m = Matter(
        firm_id=ctx.firm_id, client_id=client_id, title=body.title, practice_area=body.practice_area,
        assigned_user_ids=await validate_assignees(db, ctx.firm_id, body.assigned_user_ids),
    )
    db.add(m)
    await db.flush()
    await audit.record(db, ctx, "matter.create", target=f"matter:{m.id}")
    return _m(m)


@router.get("/clients/{client_id}/matters", response_model=Page[MatterOut])
async def list_client_matters(client_id: uuid.UUID, db: Db, ctx: Read, cursor: str | None = None, limit: Limit = None) -> Page[MatterOut]:
    rows, nxt = await keyset_page(db, select(Matter).where(Matter.client_id == client_id), Matter.id, cursor=cursor, limit=limit)
    return Page[MatterOut](items=[_m(m) for m in rows], next_cursor=nxt)


@router.get("/matters/{matter_id}", response_model=MatterOut)
async def get_matter(matter_id: uuid.UUID, db: Db, ctx: Read) -> MatterOut:
    return _m(await get_or_404(db, Matter, matter_id, "matter"))


@router.patch("/matters/{matter_id}", response_model=MatterOut)
async def patch_matter(matter_id: uuid.UUID, body: MatterPatch, db: Db, ctx: Manage) -> MatterOut:
    assert ctx.firm_id
    m = await get_or_404(db, Matter, matter_id, "matter")
    changed = body.model_dump(exclude_unset=True)
    if "assigned_user_ids" in changed and body.assigned_user_ids is not None:
        changed["assigned_user_ids"] = await validate_assignees(db, ctx.firm_id, body.assigned_user_ids)
    for k, v in changed.items():
        setattr(m, k, v)
    await audit.record(db, ctx, "matter.update", target=f"matter:{m.id}", meta={"fields": sorted(changed)})
    return _m(m)


# --- deadlines (clients may read deadlines of their own matters) -----------------------------
@router.get("/matters/{matter_id}/deadlines", response_model=Page[DeadlineOut])
async def list_deadlines(matter_id: uuid.UUID, db: Db, ctx: Read, cursor: str | None = None, limit: Limit = None) -> Page[DeadlineOut]:
    await get_or_404(db, Matter, matter_id, "matter")
    rows, nxt = await keyset_page(db, select(Deadline).where(Deadline.matter_id == matter_id), Deadline.id, cursor=cursor, limit=limit)
    return Page[DeadlineOut](items=[_d(d) for d in rows], next_cursor=nxt)


@router.post("/matters/{matter_id}/deadlines", status_code=201, response_model=DeadlineOut)
async def create_deadline(matter_id: uuid.UUID, body: DeadlineIn, db: Db, ctx: Manage) -> DeadlineOut:
    assert ctx.firm_id
    m = await get_or_404(db, Matter, matter_id, "matter")
    d = Deadline(firm_id=ctx.firm_id, client_id=m.client_id, matter_id=m.id, created_by=ctx.user_id, **body.model_dump())
    db.add(d)
    await db.flush()
    await audit.record(db, ctx, "deadline.create", target=f"deadline:{d.id}")
    return _d(d)


@router.patch("/matters/{matter_id}/deadlines/{deadline_id}", response_model=DeadlineOut)
async def patch_deadline(matter_id: uuid.UUID, deadline_id: uuid.UUID, body: DeadlinePatch, db: Db, ctx: Manage) -> DeadlineOut:
    d = await get_or_404(db, Deadline, deadline_id, "deadline")
    if d.matter_id != matter_id:
        raise NotFound("deadline")
    changed = body.model_dump(exclude_unset=True)
    for k, v in changed.items():
        setattr(d, k, v)
    await audit.record(db, ctx, "deadline.update", target=f"deadline:{d.id}", meta={"fields": sorted(changed)})
    return _d(d)


# --- tasks (firm-only) -----------------------------------------------------------------------
@router.get("/tasks", response_model=Page[TaskOut])
async def list_tasks(
    db: Db, ctx: TaskRead, status: Literal["open", "in_progress", "done"] | None = None,
    assignee_id: uuid.UUID | None = None, cursor: str | None = None, limit: Limit = None,
) -> Page[TaskOut]:
    stmt = select(Task)
    if status:
        stmt = stmt.where(Task.status == status)
    if assignee_id:
        stmt = stmt.where(Task.assignee_id == assignee_id)
    rows, nxt = await keyset_page(db, stmt, Task.id, cursor=cursor, limit=limit)
    return Page[TaskOut](items=[_t(t) for t in rows], next_cursor=nxt)


@router.post("/tasks", status_code=201, response_model=TaskOut)
async def create_task(body: TaskIn, db: Db, ctx: TaskManage) -> TaskOut:
    assert ctx.firm_id
    client_id = None
    if body.matter_id:
        client_id = (await get_or_404(db, Matter, body.matter_id, "matter")).client_id
    if body.assignee_id:
        await validate_assignees(db, ctx.firm_id, [body.assignee_id])
    t = Task(firm_id=ctx.firm_id, client_id=client_id, created_by=ctx.user_id, **body.model_dump())
    db.add(t)
    await db.flush()
    await audit.record(db, ctx, "task.create", target=f"task:{t.id}")
    return _t(t)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def patch_task(task_id: uuid.UUID, body: TaskPatch, db: Db, ctx: TaskManage) -> TaskOut:
    assert ctx.firm_id
    t = await get_or_404(db, Task, task_id, "task")
    changed = body.model_dump(exclude_unset=True)
    if changed.get("assignee_id"):
        await validate_assignees(db, ctx.firm_id, [changed["assignee_id"]])
    for k, v in changed.items():
        setattr(t, k, v)
    await audit.record(db, ctx, "task.update", target=f"task:{t.id}", meta={"fields": sorted(changed)})
    return _t(t)

