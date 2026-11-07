"""Deterministic offline LLM for local development and tests. It is NOT a language model."""
import re
from collections.abc import AsyncIterator, Callable

from app.engine.ai.providers.base import ChatMessage, LlmResult

_SOURCE = re.compile(r"^\[S(\d+)\]\s.*?<<<(.*?)>>>", re.DOTALL | re.MULTILINE)


class ExtractiveLLM:
    """Answers by quoting the first sentence of the best sources with their [S#] markers.

    This makes the full chat/guardrail path runnable without a model: the answer is, by construction, grounded.
    """

    model = "extractive-v1"

    async def generate(self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 900) -> LlmResult:
        prompt = "\n".join(m.content for m in messages if m.role == "user")
        if prompt.startswith("Summarise this conversation"):
            return LlmResult("The user asked several questions about legal documents and the law.", self.model)
        sources = _SOURCE.findall(prompt)
        if not sources:
            return LlmResult("INSUFFICIENT_SOURCES", self.model)
        q = re.search(r"QUESTION:\n(.*)\Z", prompt, re.DOTALL)
        terms = set(re.findall(r"[a-z0-9]{3,}", (q.group(1) if q else "").lower()))
        lines = []
        for n, body in sources[:3]:
            sentences = [s for s in re.split(r"(?<=[.!?])\s", " ".join(body.split())) if len(s.split()) >= 8]  # skip headings like "Section 5."
            # quote the sentence that best matches the question (ties: earliest)
            best = max(sentences, key=lambda s: len(terms & set(re.findall(r"[a-z0-9]{3,}", s.lower()))), default=" ".join(body.split())[:200])
            lines.append(f"{best.rstrip('.')} [S{n}].")
        return LlmResult(" ".join(lines), self.model, prompt_tokens=len(prompt) // 4, completion_tokens=sum(len(x) for x in lines) // 4)

    async def stream(self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 900) -> AsyncIterator[str]:
        for word in (await self.generate(messages)).text.split(" "):
            yield word + " "


class ScriptedLLM:
    """Test double: `script` is a list of replies (or callables taking the messages) consumed in order."""

    model = "scripted-test"

    def __init__(self, *script: str | Callable[[list[ChatMessage]], str] | Exception) -> None:
        self.script = list(script)
        self.calls: list[list[ChatMessage]] = []

    async def generate(self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 900) -> LlmResult:
        self.calls.append(messages)
        item = self.script.pop(0) if self.script else "INSUFFICIENT_SOURCES"
        if isinstance(item, Exception):
            raise item
        text = item(messages) if callable(item) else item
        return LlmResult(text, self.model, prompt_tokens=10, completion_tokens=len(text) // 4)

    async def stream(self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 900) -> AsyncIterator[str]:
        yield (await self.generate(messages)).text
