from app.core.config import settings
from app.engine.ai.providers.base import LLMProvider
from app.engine.ai.providers.fake import ExtractiveLLM
from app.engine.ai.providers.openai_compat import OpenAICompatLLM


def get_llm() -> LLMProvider | None:
    """FastAPI dependency; override in tests. None => AI features unavailable (503)."""
    if settings.llm_base_url and settings.llm_model_chat:
        return OpenAICompatLLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model_chat, timeout=settings.llm_timeout_s)
    return ExtractiveLLM() if settings.is_local else None
