import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers import health
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging

configure_logging(settings.log_level, json_logs=not settings.is_local)

app = FastAPI(
    title="Lygo API",
    version="0.1.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
    openapi_url="/openapi.json" if not settings.is_production else None,
)
install_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(health.router, prefix="/v1")
# Next routers (see docs/08-api-design.md): auth, me, platform, firms, members, clients,
# matters, knowledge, client_documents, documents, flags, comparisons, fact_checks, chats,
# research, reports, storage, notifications, integrations.
