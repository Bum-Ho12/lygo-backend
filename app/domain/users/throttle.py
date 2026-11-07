"""Durable, atomic rate limiting for authentication endpoints (Postgres; works across replicas and restarts).

Callers must COMMIT after recording a failure and before raising the error response, otherwise the counter rolls back with
the failed request and the limit never triggers.
"""
import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemError

_HIT = text(
    """
    INSERT INTO auth_throttle AS t (key, window_start, failures) VALUES (:k, now(), 1)
    ON CONFLICT (key) DO UPDATE SET
      failures = CASE WHEN t.window_start < now() - make_interval(secs => :w) THEN 1 ELSE t.failures + 1 END,
      window_start = CASE WHEN t.window_start < now() - make_interval(secs => :w) THEN now() ELSE t.window_start END
    RETURNING failures
    """
)
_LOCK = text("UPDATE auth_throttle SET locked_until = now() + make_interval(secs => :l) WHERE key = :k")
_REMAINING = text(
    "SELECT COALESCE(MAX(CEIL(EXTRACT(EPOCH FROM (locked_until - now())))), 0) FROM auth_throttle WHERE key = ANY(:ks) AND locked_until > now()"
)


def key(kind: str, *parts: str) -> str:
    """Stable, PII-free key: parts (emails, IPs) are hashed so the table never stores addresses."""
    digest = hashlib.sha256("|".join(p.lower() for p in parts).encode()).hexdigest()[:32]
    return f"{kind}:{digest}"


async def hit(db: AsyncSession, k: str, *, limit: int, window: int, lock: int) -> bool:
    """Count one event; once `limit` events happen inside `window` seconds the key is locked for `lock` seconds. True if locked now."""
    n = (await db.execute(_HIT, {"k": k, "w": window})).scalar_one()
    if n >= limit:
        await db.execute(_LOCK, {"k": k, "l": lock})
        return True
    return False


async def remaining(db: AsyncSession, *keys: str) -> int:
    return int((await db.execute(_REMAINING, {"ks": list(keys)})).scalar_one() or 0)


async def reset(db: AsyncSession, k: str) -> None:
    await db.execute(text("DELETE FROM auth_throttle WHERE key = :k"), {"k": k})


def too_many(seconds: int) -> ProblemError:
    return ProblemError(
        429, "too_many_attempts", "Too many attempts", f"Too many attempts. Try again in {max(1, seconds // 60 + (1 if seconds % 60 else 0))} minute(s).",
        headers={"Retry-After": str(max(1, seconds))},
    )
