"""Claude via the official Anthropic SDK.

Rules this adapter follows for the current Claude 5.x models (see the claude-api skill / docs):
  * no sampling parameters (temperature/top_p/top_k are removed and 400);
  * thinking is left to the model (Opus 5.5 always thinks; `effort` is the dial) and thinking tokens count against `max_tokens`,
    so we never pass a small ceiling;
  * `stop_reason == "refusal"` is surfaced as LlmRefused, never read as content;
  * models that support it get the server-side refusal fallback (`fallbacks="default"`, beta header below).
"""
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import structlog

from app.engine.ai.providers.base import (
    ChatMessage,
    LlmError,
    LlmRefused,
    LlmResult,
    LlmUnavailable,
)

log = structlog.get_logger()

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}  # not Haiku 5.5: no server-side fallback
MIN_OUTPUT_TOKENS = 4096  # headroom for adaptive thinking; the visible answer is far shorter
MAX_OUTPUT_TOKENS = 16000  # keep non-streaming requests under SDK HTTP timeouts


class AnthropicLLM:
    def __init__(
        self, api_key: str, model: str = "claude-opus-5-5", *, effort: str = "medium", fallbacks: bool = True,
        client: anthropic.AsyncAnthropic | None = None, max_retries: int = 3, timeout: float = 120.0,
    ) -> None:
        if not api_key and client is None:
            raise LlmError("ANTHROPIC_API_KEY is not set")
        self.model, self.effort = model, effort
        self.use_fallbacks = fallbacks and model in _FALLBACK_MODELS
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=max_retries, timeout=timeout)

    # -- request building ---------------------------------------------------------------------------------------------
    def _request(self, messages: list[ChatMessage], max_tokens: int, effort: str | None = None) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        while turns and turns[0]["role"] != "user":  # the API requires the first turn to be the user's
            turns.pop(0)
        if not turns:
            raise LlmError("no user turn to send")
        req: dict[str, Any] = {"model": self.model, "max_tokens": min(max(max_tokens, MIN_OUTPUT_TOKENS), MAX_OUTPUT_TOKENS), "messages": turns}
        if system:
            req["system"] = system
        if effort or self.effort:
            req["output_config"] = {"effort": effort or self.effort}
        return req

    # -- calls ------------------------------------------------------------------------------------------------------------------
    async def generate(
        self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 900, effort: str | None = None
    ) -> LlmResult:
        """`temperature` is accepted for interface compatibility and deliberately ignored (removed on Claude 5.x)."""
        req = self._request(messages, max_tokens, effort)
        try:
            if self.use_fallbacks:
                resp = await self._client.beta.messages.create(**req, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                resp = await self._client.messages.create(**req)
        except anthropic.APIError as exc:
            raise self._map(exc) from exc
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise LlmRefused(getattr(details, "category", None))
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LlmResult(
            text=text, model=str(resp.model), prompt_tokens=int(resp.usage.input_tokens), completion_tokens=int(resp.usage.output_tokens),
            finish_reason=str(resp.stop_reason or "stop"),
        )

    async def stream(self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 900) -> AsyncIterator[str]:
        req = self._request(messages, max_tokens)
        try:
            async with self._client.messages.stream(**req) as s:
                async for chunk in s.text_stream:
                    yield chunk
                final = await s.get_final_message()
        except anthropic.APIError as exc:
            raise self._map(exc) from exc
        if final.stop_reason == "refusal":
            raise LlmRefused(getattr(getattr(final, "stop_details", None), "category", None))

    @staticmethod
    def _map(exc: anthropic.APIError) -> LlmError:
        rid = getattr(exc, "request_id", None)
        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            log.error("llm.auth_failed", status=getattr(exc, "status_code", None), request_id=rid, detail="check ANTHROPIC_API_KEY and model access")
            return LlmError("the language model rejected our credentials")  # operator problem: do not leak details to users
        if isinstance(exc, anthropic.RateLimitError | anthropic.InternalServerError | anthropic.APIConnectionError):
            return LlmUnavailable(f"the language model is unavailable ({type(exc).__name__})")
        if isinstance(exc, anthropic.APIStatusError) and exc.status_code in (408, 409, 429, 500, 502, 503, 504, 529):
            return LlmUnavailable(f"the language model is overloaded ({exc.status_code})")  # includes 529 "overloaded"
        log.warning("llm.request_rejected", status=getattr(exc, "status_code", None), request_id=rid)
        return LlmError("the language model rejected the request")
