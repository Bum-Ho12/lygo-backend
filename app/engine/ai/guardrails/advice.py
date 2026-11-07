"""Advice boundary for the client audience (docs/04 guardrail 2; Advocates Act ss.33-34, docs/07).

The client chatbot explains documents and the law; it does not tell clients what to do or draft instruments.
"""
import re
from dataclasses import dataclass

_ADVICE_REQUEST = [
    ("decision", re.compile(r"\bshould\s+i\s+(?:sue|appeal|settle|sign|accept|reject|refuse|plead|file|pay|withdraw|proceed|go\s+to\s+court|hire|take)\b", re.IGNORECASE)),
    ("decision", re.compile(r"\b(?:do\s+you\s+(?:think|advise|recommend)|would\s+you\s+(?:advise|recommend))\b", re.IGNORECASE)),
    ("decision", re.compile(r"\bwhat\s+(?:should|do)\s+i\s+do\b", re.IGNORECASE)),
    ("prediction", re.compile(r"\b(?:will|can)\s+i\s+(?:win|lose)\b|\bmy\s+chances\b|\bhow\s+likely\b|\bam\s+i\s+going\s+to\s+win\b", re.IGNORECASE)),
    ("drafting", re.compile(r"\b(?:draft|write|prepare|compose|create)\s+(?:me\s+)?(?:a|an|my|the)\s+(?:\w+\s+){0,2}(?:appeal|petition|plaint|pleading|affidavit|contract|agreement|will|lease|notice|demand\s+letter|defen[cs]e|application|power\s+of\s+attorney)\b", re.IGNORECASE)),
    ("advice", re.compile(r"\b(?:advise|advice)\s+me\b|\bgive\s+me\s+(?:legal\s+)?advice\b|\blegal\s+strategy\b", re.IGNORECASE)),
]
_DIRECTIVE_OUTPUT = re.compile(
    # Advisory phrasing only. Obligation wording ("you must pay by ...", "you must file within 14 days") is information
    # taken from the document and is deliberately NOT stripped.
    r"\byou\s+(?:should|ought\s+to)\s+(?:sue|appeal|file|sign|accept|reject|refuse|settle|plead|withdraw|pay|go\s+to\s+court|hire|take|proceed|consider)\b"
    r"|\bi\s+(?:recommend|advise|suggest)\b|\bmy\s+advice\b|\bthe\s+best\s+(?:course|option)\s+(?:for\s+you\s+)?is\b|\byou\s+will\s+(?:win|lose)\b",
    re.IGNORECASE,
)
DISCLAIMER = "This is general information to help you understand your documents, not legal advice. Your advocate can advise you on what to do."
ESCALATION = "I've passed your question to your advocate, who can advise you on your situation."


@dataclass(frozen=True)
class AdviceCheck:
    advice_seeking: bool
    category: str = ""


def check_question(question: str) -> AdviceCheck:
    for category, pat in _ADVICE_REQUEST:
        if pat.search(question):
            return AdviceCheck(True, category)
    return AdviceCheck(False)


def strip_directives(answer: str) -> tuple[str, int]:
    """Remove sentences where the model slipped into giving advice. Returns (clean text, removed count)."""
    kept, removed = [], 0
    for s in re.split(r"(?<=[.!?])\s+", answer):
        if _DIRECTIVE_OUTPUT.search(s):
            removed += 1
        else:
            kept.append(s)
    return " ".join(kept).strip(), removed
