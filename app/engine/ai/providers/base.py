"""LLM provider interface (docs/04). Model choice is configuration, not code."""
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol


class LlmError(Exception): ...


class LlmUnavailable(LlmError):
    """Transient: the provider is down or rate-limiting after retries."""


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LlmResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"


class LLMProvider(Protocol):
    model: str

    async def generate(self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 900) -> LlmResult: ...
    def stream(self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 900) -> AsyncIterator[str]: ...
