import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


async def check_db() -> bool:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    finally:
        await engine.dispose()


async def check_redis() -> bool:
    client = Redis.from_url(settings.redis_url)
    try:
        return bool(await client.ping())
    finally:
        await client.aclose()


async def _safe(check) -> bool:  # type: ignore[no-untyped-def]
    try:
        return await asyncio.wait_for(check(), timeout=2)
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        return False


@router.get("/health/ready")
async def ready() -> JSONResponse:
    """Readiness: dependencies reachable."""
    db, redis = await asyncio.gather(_safe(check_db), _safe(check_redis))
    ok = db and redis
    return JSONResponse(
        {"status": "ok" if ok else "unavailable", "checks": {"db": db, "redis": redis}},
        status_code=200 if ok else 503,
    )
