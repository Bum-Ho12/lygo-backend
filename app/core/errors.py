"""RFC 9457 problem+json errors. See docs/08-api-design.md."""
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class ProblemError(Exception):
    def __init__(
        self, status: int, code: str, title: str, detail: str = "", extra: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.detail, self.extra = status, code, title, detail, extra or {}
        self.headers = headers or {}


class NotFound(ProblemError):
    def __init__(self, what: str = "resource") -> None:
        super().__init__(404, "not_found", "Not found", f"The requested {what} was not found.")


class Forbidden(ProblemError):
    def __init__(self, detail: str = "You do not have permission to perform this action.") -> None:
        super().__init__(403, "forbidden", "Forbidden", detail)


def _problem(request: Request, status: int, code: str, title: str, detail: str, **extra: Any) -> JSONResponse:
    body = {
        "type": f"https://docs.lygo.app/errors/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "instance": request.url.path,
        **extra,
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem_error(request: Request, exc: ProblemError) -> JSONResponse:
        resp = _problem(request, exc.status, exc.code, exc.title, exc.detail, **exc.extra)
        resp.headers.update(exc.headers)
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return _problem(request, 422, "validation_error", "Validation failed", "Request validation failed.", errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return _problem(request, exc.status_code, code, str(exc.detail), str(exc.detail))

    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    @app.exception_handler(ConnectionRefusedError)
    async def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
        # Database down / unreachable: a clear, retryable 503 instead of a stack-trace 500.
        structlog.get_logger().error("database.unavailable", error=type(exc).__name__, detail=str(exc).splitlines()[0][:200])
        resp = _problem(request, 503, "database_unavailable", "Service unavailable", "The database is unavailable. Please try again shortly.")
        resp.headers["Retry-After"] = "5"
        return resp

    # Safety net for third-party outages that escape a call site: always a clean, retryable 503, never a stack-trace 500.
    from app.engine.ai.providers.base import LlmError, LlmRefused
    from app.engine.rag.embeddings.base import EmbeddingError
    from app.integrations.email.base import EmailError
    from app.integrations.sms.base import SmsError
    from app.storage.base import ObjectNotFound, StorageError

    _OUTAGES: list[tuple[type[Exception], str, str]] = [
        (StorageError, "storage_unavailable", "Document storage is unavailable. Please try again shortly."),
        (EmbeddingError, "search_unavailable", "Search is temporarily unavailable. Please try again shortly."),
        (LlmError, "ai_unavailable", "The assistant is temporarily unavailable. Please try again shortly."),
        (SmsError, "sms_unavailable", "Text messages cannot be sent right now. Please try again shortly."),
        (EmailError, "email_unavailable", "Email cannot be sent right now. Please try again shortly."),
    ]

    def _outage_handler(code: str, detail: str):  # type: ignore[no-untyped-def]
        async def handler(request: Request, exc: Exception) -> JSONResponse:
            if isinstance(exc, ObjectNotFound):
                return _problem(request, 404, "not_found", "Not found", "The requested file was not found.")
            if isinstance(exc, LlmRefused):
                return _problem(request, 422, "ai_declined", "Request declined", "The assistant declined this request.")
            structlog.get_logger().error("dependency.unavailable", code=code, error=type(exc).__name__, path=request.url.path)
            resp = _problem(request, 503, code, "Service unavailable", detail)
            resp.headers["Retry-After"] = "10"
            return resp

        return handler

    for exc_type, code, detail in _OUTAGES:
        app.add_exception_handler(exc_type, _outage_handler(code, detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals; details go to logs only.
        return _problem(request, 500, "internal_error", "Internal error", "An unexpected error occurred.")
