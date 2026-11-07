"""RFC 9457 problem+json errors. See docs/08-api-design.md."""
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals; details go to logs only.
        return _problem(request, 500, "internal_error", "Internal error", "An unexpected error occurred.")
