"""Durable job queue on PostgreSQL (replaces Celery + Redis; see docs/adr/0008).

Guarantees:
  * enqueue() runs inside the caller's transaction, so a job exists if and only if the work that needs it committed
    (no lost jobs between "commit" and "dispatch", no orphan jobs after a rollback);
  * claim() uses FOR UPDATE SKIP LOCKED: many workers, each job handed to exactly one of them;
  * a claimed job holds a lease; a worker that dies stops renewing it and the job is re-queued (attempts are counted at claim time);
  * complete()/fail()/extend() are fenced by `locked_by`, so a worker whose lease was lost cannot overwrite the new owner's result.
Payloads must contain ids only, never document content or personal data.
"""
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NOTIFY_CHANNEL = "lygo_jobs"
MAX_ERROR_CHARS = 500


@dataclass(frozen=True)
class Job:
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    firm_id: uuid.UUID | None
    attempts: int
    max_attempts: int


async def enqueue(
    db: AsyncSession, kind: str, payload: dict[str, Any] | None = None, *, firm_id: uuid.UUID | None = None, run_at: datetime | None = None,
    dedupe_key: str | None = None, max_attempts: int = 5, priority: int = 0,
) -> uuid.UUID | None:
    """Insert a job in the caller's transaction. Returns None if an identical live job (same dedupe_key) already exists."""
    row = (
        await db.execute(
            text(
                "INSERT INTO jobs (kind, payload, firm_id, run_at, dedupe_key, max_attempts, priority) "
                "VALUES (:k, CAST(:p AS jsonb), :f, COALESCE(:r, now()), :d, :m, :pr) "
                "ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running') DO NOTHING RETURNING id"
            ),
            {"k": kind, "p": json.dumps(payload or {}), "f": firm_id, "r": run_at, "d": dedupe_key, "m": max_attempts, "pr": priority},
        )
    ).first()
    await db.execute(text("SELECT pg_notify(:c, :k)"), {"c": NOTIFY_CHANNEL, "k": kind})  # delivered at COMMIT: wakes idle workers
    return row[0] if row else None


async def claim(factory: async_sessionmaker[AsyncSession], worker_id: str, kinds: list[str], lease_seconds: float) -> Job | None:
    async with factory() as s, s.begin():
        row = (
            await s.execute(
                text(
                    "UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_by = :w, "
                    "locked_until = now() + make_interval(secs => :l), updated_at = now() "
                    "WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_at <= now() AND kind = ANY(:ks) "
                    "            ORDER BY priority DESC, run_at, id FOR UPDATE SKIP LOCKED LIMIT 1) "
                    "RETURNING id, kind, payload, firm_id, attempts, max_attempts"
                ),
                {"w": worker_id, "l": lease_seconds, "ks": kinds},
            )
        ).first()
    return Job(row.id, row.kind, row.payload, row.firm_id, row.attempts, row.max_attempts) if row else None


async def extend(factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID, worker_id: str, lease_seconds: float) -> bool:
    async with factory() as s, s.begin():
        r = await s.execute(
            text("UPDATE jobs SET locked_until = now() + make_interval(secs => :l) WHERE id = :i AND locked_by = :w AND status = 'running' RETURNING id"),
            {"l": lease_seconds, "i": job_id, "w": worker_id},
        )
        return r.first() is not None


async def complete(factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID, worker_id: str) -> bool:
    async with factory() as s, s.begin():
        r = await s.execute(
            text("UPDATE jobs SET status = 'done', locked_by = NULL, locked_until = NULL, finished_at = now(), updated_at = now() WHERE id = :i AND locked_by = :w AND status = 'running' RETURNING id"),
            {"i": job_id, "w": worker_id},
        )
        return r.first() is not None


def backoff_seconds(attempts: int) -> float:
    return float(min(900, 5 * 2 ** max(0, attempts - 1)))  # 5s, 10s, 20s ... capped at 15 minutes


async def fail(
    factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID, worker_id: str, error: str, *, permanent: bool = False, delay: float | None = None
) -> str | None:
    """Record a failure. Returns the new status ('queued' for a retry, 'dead' when out of attempts or permanent), None if we lost the lease."""
    async with factory() as s, s.begin():
        r = await s.execute(
            text(
                "UPDATE jobs SET status = CASE WHEN :perm OR attempts >= max_attempts THEN 'dead' ELSE 'queued' END, "
                "run_at = now() + make_interval(secs => COALESCE(:d, LEAST(900, 5 * power(2, GREATEST(attempts - 1, 0))))), "
                "last_error = :e, locked_by = NULL, locked_until = NULL, updated_at = now(), "
                "finished_at = CASE WHEN :perm OR attempts >= max_attempts THEN now() END "
                "WHERE id = :i AND locked_by = :w AND status = 'running' RETURNING status"
            ),
            {"perm": permanent, "d": delay, "e": error[:MAX_ERROR_CHARS], "i": job_id, "w": worker_id},
        )
        row = r.first()
        return str(row[0]) if row else None


async def reap_expired(factory: async_sessionmaker[AsyncSession]) -> int:
    """Re-queue (or kill, if out of attempts) jobs whose worker stopped renewing its lease: crash recovery."""
    async with factory() as s, s.begin():
        r = await s.execute(
            text(
                "UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END, locked_by = NULL, locked_until = NULL, "
                "last_error = 'worker lease expired (crash or stall)', updated_at = now(), "
                "finished_at = CASE WHEN attempts >= max_attempts THEN now() END "
                "WHERE status = 'running' AND locked_until < now() RETURNING id"
            )
        )
        return len(r.all())


async def due_periodic(factory: async_sessionmaker[AsyncSession], kind: str, interval_seconds: float) -> bool:
    """Atomically decide whether THIS worker should enqueue the periodic job now (at most one worker wins per interval)."""
    async with factory() as s, s.begin():
        r = await s.execute(
            text(
                "INSERT INTO job_schedule AS t (kind, last_run) VALUES (:k, now()) ON CONFLICT (kind) DO UPDATE SET last_run = now() "
                "WHERE t.last_run <= now() - make_interval(secs => :i) RETURNING kind"
            ),
            {"k": kind, "i": interval_seconds},
        )
        return r.first() is not None


async def stats(factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    async with factory() as s, s.begin():
        rows = (await s.execute(text("SELECT kind, status, count(*) FROM jobs GROUP BY 1, 2 ORDER BY 1, 2"))).all()
        oldest = (await s.execute(text("SELECT COALESCE(EXTRACT(EPOCH FROM (now() - min(run_at))), 0) FROM jobs WHERE status = 'queued' AND run_at <= now()"))).scalar_one()
    by_kind: dict[str, dict[str, int]] = {}
    for kind, status, n in rows:
        by_kind.setdefault(kind, {})[status] = int(n)
    return {"by_kind": by_kind, "oldest_runnable_age_seconds": round(float(oldest), 1)}


async def dead_jobs(factory: async_sessionmaker[AsyncSession], limit: int = 50) -> list[dict[str, Any]]:
    async with factory() as s, s.begin():
        rows = (await s.execute(text("SELECT id, kind, firm_id, attempts, last_error, finished_at FROM jobs WHERE status = 'dead' ORDER BY finished_at DESC LIMIT :n"), {"n": limit})).all()
    return [{"id": str(r.id), "kind": r.kind, "firm_id": str(r.firm_id) if r.firm_id else None, "attempts": r.attempts, "last_error": r.last_error, "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in rows]


async def retry_dead(factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> bool:
    async with factory() as s, s.begin():
        r = await s.execute(
            text("UPDATE jobs SET status = 'queued', attempts = 0, run_at = now(), finished_at = NULL, updated_at = now() WHERE id = :i AND status = 'dead' RETURNING id"),
            {"i": job_id},
        )
        queued = r.first() is not None
        if queued:
            await s.execute(text("SELECT pg_notify(:c, 'retry')"), {"c": NOTIFY_CHANNEL})
        return queued


async def purge(factory: async_sessionmaker[AsyncSession], *, done_days: int = 7, dead_days: int = 60) -> int:
    async with factory() as s, s.begin():
        r = await s.execute(
            text(
                "DELETE FROM jobs WHERE (status = 'done' AND finished_at < now() - make_interval(days => :a)) "
                "OR (status = 'dead' AND finished_at < now() - make_interval(days => :b)) RETURNING id"
            ),
            {"a": done_days, "b": dead_days},
        )
        return len(r.all())
