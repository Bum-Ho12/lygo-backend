"""Conversation memory: the last N turns verbatim + a rolling summary of older ones (docs/04 §4)."""
from app.engine.ai.providers.base import ChatMessage, LLMProvider

KEEP_VERBATIM = 8
SUMMARISE_AFTER = 16
SUMMARISE_STEP = 8
MAX_CHARS_PER_TURN = 1500


def to_history(rows: list[tuple[str, str]]) -> list[ChatMessage]:
    """(role, content) oldest->newest -> provider messages, trimmed."""
    return [ChatMessage("user" if role == "user" else "assistant", content[:MAX_CHARS_PER_TURN]) for role, content in rows[-KEEP_VERBATIM:]]


def needs_summary(total_messages: int, summarised_upto: int) -> bool:
    return total_messages > SUMMARISE_AFTER and (total_messages - KEEP_VERBATIM) - summarised_upto >= SUMMARISE_STEP


async def summarise(llm: LLMProvider, previous: str, older: list[tuple[str, str]]) -> str:
    transcript = "\n".join(f"{role.upper()}: {content[:600]}" for role, content in older)
    prompt = (
        "Summarise this conversation between a user and a legal-research assistant in under 120 words: the topics discussed and any "
        "facts the user stated. Do not add legal conclusions or citations that are not already in the text.\n\n"
        + (f"PREVIOUS SUMMARY:\n{previous}\n\n" if previous else "") + f"CONVERSATION:\n{transcript}"
    )
    res = await llm.generate([ChatMessage("user", prompt)], max_tokens=250)
    return res.text.strip()[:1200]
