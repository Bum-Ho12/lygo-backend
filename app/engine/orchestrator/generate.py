"""The AI engine core: build prompt -> LLM -> verify citations -> (retry once) -> abstain if ungrounded.
Pure (no database): the same path serves chat, research and summaries."""
from dataclasses import dataclass, field

from app.engine.ai.guardrails.advice import strip_directives
from app.engine.ai.guardrails.citations import Removed, Source, verify
from app.engine.ai.guardrails.grounding import INSUFFICIENT
from app.engine.ai.prompts.templates import build_prompt
from app.engine.ai.providers.base import ChatMessage, LLMProvider
from app.engine.ai.settings import AiSettings

_RETRY_NOTE = (
    "Your previous answer contained statements the sources do not support. Answer again using ONLY the sources, cite every "
    "sentence with its [S#] marker, and reply INSUFFICIENT_SOURCES if the sources do not answer."
)


@dataclass
class Generated:
    status: str  # ok | abstained
    text: str = ""
    used: list[int] = field(default_factory=list)
    removed: list[Removed] = field(default_factory=list)
    directives_removed: int = 0
    injection_hits: int = 0
    prompt_version: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    attempts: int = 0
    abstain_reason: str = ""


async def generate_verified(
    llm: LLMProvider, task: str, audience: str, question: str, sources: list[Source], ai: AiSettings, *,
    history: list[ChatMessage] | None = None, facts: list[str] | None = None, summary: str = "", extra_instruction: str = "",
) -> Generated:
    out = Generated(status="abstained")
    if not sources:
        out.abstain_reason = "no_sources"
        return out
    note = extra_instruction
    for attempt in (1, 2):
        built = build_prompt(task, audience, question, sources, ai, history=history, facts=facts, summary=summary, extra_instruction=note)
        res = await llm.generate(built.messages)
        out.attempts, out.model, out.prompt_version = attempt, res.model, built.version
        out.prompt_tokens += res.prompt_tokens
        out.completion_tokens += res.completion_tokens
        out.injection_hits = max(out.injection_hits, built.injection_hits)
        if res.text.strip().startswith(INSUFFICIENT):
            out.abstain_reason = "model_declined"
            return out
        v = verify(res.text, sources)
        out.removed = v.removed
        dropped_most = len(v.removed) > len(v.text.split(". ")) + 1
        if v.ok and not dropped_most:
            text, n = strip_directives(v.text) if audience == "client" else (v.text, 0)
            out.directives_removed = n
            if audience == "client" and (n and not verify(text, sources).ok):
                out.abstain_reason = "only_advice_remained"
                return out
            out.status, out.text, out.used = "ok", text, v.used
            return out
        note = f"{extra_instruction}\n{_RETRY_NOTE}".strip()
    out.abstain_reason = "unverifiable"
    return out
