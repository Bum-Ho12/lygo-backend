import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers import (
    account,
    ai,
    auth,
    clients,
    documents,
    firms,
    health,
    intelligence,
    invitations,
    matters,
    members,
    notifications,
    platform,
    search,
    signup,
    storage,
)
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging

configure_logging(settings.log_level, json_logs=not settings.is_local)


def _log_config_summary() -> None:
    """One line at startup saying which external integrations are live, so a silent fallback is visible immediately."""
    from app.integrations.sms import factory as sms_factory

    structlog.get_logger().info(
        "config.loaded", env=settings.app_env, env_file_found=(settings.model_config["env_file"]).exists(),  # type: ignore[union-attr]
        sms=sms_factory.describe(), llm="anthropic" if settings.anthropic_api_key else "none/local", email="smtp" if settings.smtp_host else "disabled",
    )


_log_config_summary()

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
app.include_router(auth.router, prefix="/v1")
app.include_router(account.router, prefix="/v1")
app.include_router(firms.router, prefix="/v1")
app.include_router(members.router, prefix="/v1")
app.include_router(invitations.router, prefix="/v1")
app.include_router(clients.router, prefix="/v1")
app.include_router(matters.router, prefix="/v1")
app.include_router(documents.router, prefix="/v1")
app.include_router(notifications.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")
app.include_router(ai.router, prefix="/v1")
app.include_router(intelligence.router, prefix="/v1")
app.include_router(storage.router, prefix="/v1")
app.include_router(platform.router, prefix="/v1")
app.include_router(signup.router, prefix="/v1")
# Next routers (see docs/08-api-design.md): auth, me, platform, firms, members, clients,
# matters, knowledge, client_documents, documents, flags, comparisons, fact_checks, chats,
# research, reports, storage, notifications, integrations.
