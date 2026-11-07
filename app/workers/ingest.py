"""Ingestion dispatch: in-process for local/dev, Celery for real deployments."""
import asyncio
import uuid
from typing import Annotated, Protocol

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import Factory
from app.core.config import settings
from app.engine.rag.embeddings.base import Embedder
from app.engine.rag.embeddings.factory import get_embedder
from app.ingestion.ocr.base import OcrEngine, get_ocr
from app.ingestion.pipeline import process_document
from app.storage.base import StorageDestination
from app.storage.factory import default_destination, get_storage
from app.workers.celery_app import celery_app


class Dispatcher(Protocol):
    async def dispatch(self, firm_id: uuid.UUID, document_id: uuid.UUID) -> None: ...


_TASKS: set[asyncio.Task[str]] = set()  # strong refs so fire-and-forget tasks are not garbage collected


class InlineDispatcher:
    """Runs the pipeline as a background asyncio task in the API process (local/dev only)."""

    def __init__(
        self, factory: async_sessionmaker[AsyncSession], storage: StorageDestination, ocr: OcrEngine, embedder: Embedder | None = None
    ) -> None:
        self.factory, self.storage, self.ocr, self.embedder = factory, storage, ocr, embedder

    async def dispatch(self, firm_id: uuid.UUID, document_id: uuid.UUID) -> None:
        t = asyncio.create_task(process_document(self.factory, self.storage, self.ocr, firm_id, document_id, self.embedder))
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)


class CeleryDispatcher:
    async def dispatch(self, firm_id: uuid.UUID, document_id: uuid.UUID) -> None:
        process_document_task.delay(str(firm_id), str(document_id))


@celery_app.task(name="app.workers.ingest.process_document", acks_late=True, autoretry_for=(OSError,), retry_backoff=True, max_retries=3)  # type: ignore[untyped-decorator]
def process_document_task(firm_id: str, document_id: str) -> str:
    async def run() -> str:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)  # fresh loop => no shared pool
        try:
            return await process_document(
                async_sessionmaker(engine, expire_on_commit=False), default_destination(), get_ocr(),
                uuid.UUID(firm_id), uuid.UUID(document_id), get_embedder(),
            )
        finally:
            await engine.dispose()

    return asyncio.run(run())


def get_dispatcher(
    factory: Factory, storage: Annotated[StorageDestination, Depends(get_storage)], ocr: Annotated[OcrEngine, Depends(get_ocr)],
    embedder: Annotated[Embedder | None, Depends(get_embedder)],
) -> Dispatcher:
    """FastAPI dependency; override in tests."""
    return CeleryDispatcher() if settings.ingest_mode == "celery" else InlineDispatcher(factory, storage, ocr, embedder)
