from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers import health
from app.core.config import settings

app = FastAPI(title="Lygo API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/v1")
# Next routers (see docs/08-api-design.md): auth, me, platform, firms, members, clients,
# matters, knowledge, client_documents, documents, flags, chats, research, reports,
# storage, notifications, integrations.
