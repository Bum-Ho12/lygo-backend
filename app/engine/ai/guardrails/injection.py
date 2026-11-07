"""Prompt-injection defence for retrieved text (docs/04 guardrail 5): sources are DATA, never instructions."""
import re

_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|rules?|prompts?|messages?)", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier|system)\s+(?:instructions?|rules?|prompts?)", re.IGNORECASE),
    re.compile(r"forget\s+(?:everything|all|your)\s+(?:above|previous|instructions|rules)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a|an|in)\b", re.IGNORECASE),
    re.compile(r"(?:reveal|print|show|repeat)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions|rules)", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"</?\s*(?:system|assistant|user|instructions?)\s*>", re.IGNORECASE),
    re.compile(r"\[/?(?:INST|SYS)\]", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:system|assistant)\s*:", re.IGNORECASE),
]
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
REPLACEMENT = "[removed: instruction-like text]"


def neutralise(text: str) -> tuple[str, int]:
    """Return (safe text, number of instruction-like spans removed). Also strips our own delimiters and control chars."""
    text = _CONTROL.sub("", text).replace("<<<", "«").replace(">>>", "»")
    hits = 0
    for p in _PATTERNS:
        text, n = p.subn(REPLACEMENT, text)
        hits += n
    return text, hits
