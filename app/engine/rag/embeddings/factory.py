from app.core.config import settings
from app.engine.rag.embeddings.base import Embedder, HashingEmbedder
from app.engine.rag.embeddings.openai_compat import OpenAICompatEmbedder


def get_embedder() -> Embedder | None:
    """FastAPI/worker dependency; override in tests. None => indexing/search unavailable (no model configured)."""
    if settings.llm_base_url and settings.embedding_model:
        return OpenAICompatEmbedder(
            settings.llm_base_url, settings.llm_api_key, settings.embedding_model, settings.embedding_dim,
            batch_size=settings.embed_batch_size,
        )
    return HashingEmbedder() if settings.is_local else None
