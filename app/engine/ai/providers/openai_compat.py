"""OpenAI-compatible chat completions (RumptyCloud inference, OpenAI, vLLM, Ollama...)."""
import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

from app.engine.ai.providers.base import ChatMessage, LlmError, LlmResult, LlmUnavailable

_RETRY = {429, 500, 502, 503, 504}


class OpenAICompatLLM:
    def __init__(
        self, base_url: str, api_key: str, model: str, *, client: httpx.AsyncClient | None = None, timeout: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, max_attempts: int = 3,
    ) -> None:
        self.model, self._sleep, self._attempts = model, sleep, max_attempts
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def _payload(self, messages: list[ChatMessage], temperature: float, max_tokens: int, stream: bool) -> dict:  # type: ignore[type-arg]
        return {
            "model": self.model, "temperature": temperature, "max_tokens": max_tokens, "stream": stream,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

    async def generate(
        self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 900, effort: str | None = None
    ) -> LlmResult:
        last: Exception | None = None
        for attempt in range(self._attempts):
            try:
                r = await self._client.post("/chat/completions", json=self._payload(messages, temperature, max_tokens, False), headers=self._headers)
            except httpx.HTTPError as exc:
                last = exc
            else:
                if r.status_code == 200:
                    return self._parse(r.json())
                if r.status_code not in _RETRY:
                    raise LlmError(f"LLM request rejected ({r.status_code})")
                last = LlmUnavailable(f"LLM service error ({r.status_code})")
            await self._sleep(2**attempt)
        raise LlmUnavailable(f"LLM unavailable after {self._attempts} attempts: {last}")

    def _parse(self, body: dict) -> LlmResult:  # type: ignore[type-arg]
        try:
            choice = body["choices"][0]
            usage = body.get("usage") or {}
            return LlmResult(
                text=str(choice["message"]["content"] or ""), model=str(body.get("model", self.model)),
                prompt_tokens=int(usage.get("prompt_tokens", 0)), completion_tokens=int(usage.get("completion_tokens", 0)),
                finish_reason=str(choice.get("finish_reason") or "stop"),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmError("malformed LLM response") from exc

    async def stream(self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 900) -> AsyncIterator[str]:
        try:
            async with self._client.stream("POST", "/chat/completions", json=self._payload(messages, temperature, max_tokens, True), headers=self._headers) as r:
                if r.status_code != 200:
                    raise LlmUnavailable(f"LLM stream failed ({r.status_code})")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError, ValueError):
                        continue
                    if delta:
                        yield str(delta)
        except httpx.HTTPError as exc:
            raise LlmUnavailable(f"LLM stream error: {exc}") from exc
