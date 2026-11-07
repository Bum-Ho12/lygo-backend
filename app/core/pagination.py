"""Cursor pagination helpers: `?cursor=&limit=` -> `{items, next_cursor}`."""
import base64
import json
from typing import Any

from pydantic import BaseModel

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
    except Exception as exc:  # malformed input from client
        raise ProblemError(400, "invalid_cursor", "Invalid cursor", "The cursor is malformed.") from exc
    if not isinstance(data, dict):
        raise ProblemError(400, "invalid_cursor", "Invalid cursor", "The cursor is malformed.")
    return data


def clamp_limit(limit: int | None, default: int = 50) -> int:
    return max(1, min(limit or default, MAX_LIMIT))
