from functools import lru_cache

from app.core.config import settings
from app.engine.ai.providers.anthropic_llm import AnthropicLLM
from app.engine.ai.providers.base import LLMProvider
from app.engine.ai.providers.fake import ExtractiveLLM
from app.engine.ai.providers.openai_compat import OpenAICompatLLM


@lru_cache
def _anthropic() -> AnthropicLLM:
    return AnthropicLLM(settings.anthropic_api_key, settings.anthropic_model, effort=settings.anthropic_effort, fallbacks=settings.anthropic_fallbacks)


def get_llm() -> LLMProvider | None:
    """FastAPI dependency; override in tests. None => AI features unavailable (503)."""
    if settings.anthropic_api_key:
        return _anthropic()  # one pooled client for the process
    if settings.llm_base_url and settings.llm_model_chat:
        return OpenAICompatLLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model_chat, timeout=settings.llm_timeout_s)
    return ExtractiveLLM() if settings.is_local else None
