"""Cursor pagination helpers: `?cursor=&limit=` -> `{items, next_cursor}`."""
import base64
import json
import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import ProblemError

MAX_LIMIT = 200


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception as exc:
        raise ProblemError(400, "invalid_cursor", "Invalid cursor", "The cursor is malformed.") from exc
    if not isinstance(data, dict):
        raise ProblemError(400, "invalid_cursor", "Invalid cursor", "The cursor is malformed.")
    return data


def clamp_limit(limit: int | None, default: int = 50) -> int:
    return max(1, min(limit or default, MAX_LIMIT))


async def keyset_page(
    db: AsyncSession, stmt: Select[Any], id_col: InstrumentedAttribute[uuid.UUID], *, cursor: str | None, limit: int | None
) -> tuple[list[Any], str | None]:
    """Order `stmt` by `id_col` and return (rows, next_cursor). Rows are ORM objects (first column)."""
    n = clamp_limit(limit)
    after = decode_cursor(cursor)
    if after:
        try:
            stmt = stmt.where(id_col > uuid.UUID(str(after.get("after"))))
        except ValueError as exc:
            raise ProblemError(400, "invalid_cursor", "Invalid cursor", "The cursor is malformed.") from exc
    rows = list((await db.execute(stmt.order_by(id_col).limit(n + 1))).scalars())
    more = len(rows) > n
    rows = rows[:n]
    return rows, (encode_cursor({"after": str(rows[-1].id)}) if more and rows else None)
