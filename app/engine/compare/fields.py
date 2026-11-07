"""Typed value extraction used to spot material changes (amounts, periods, percentages, dates)."""
import re
from collections import Counter

from app.engine.compare.models import FieldChange

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_PATTERNS: dict[str, re.Pattern[str]] = {
    "money": re.compile(r"(?:KES|Ksh\.?|KSh\.?|USD|EUR|GBP|[$£€])\s?\d[\d,]*(?:\.\d+)?", re.IGNORECASE),
    "duration": re.compile(r"\b\d+\s*(?:business\s+|working\s+|calendar\s+)?(?:day|week|month|year)s?\b", re.IGNORECASE),
    "percent": re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent)", re.IGNORECASE),
    "date": re.compile(rf"\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})\b", re.IGNORECASE),
}
_NEGATIONS = re.compile(r"\b(?:not|no|never|neither|nor|without|except|excluding)\b", re.IGNORECASE)
_MODALS = re.compile(r"\b(shall|must|may|should|will)\b", re.IGNORECASE)


def _norm(kind: str, v: str) -> str:
    v = re.sub(r"\s+", " ", v.strip().lower())
    return v.replace(",", "") if kind == "money" else v


def extract_fields(text: str) -> dict[str, Counter[str]]:
    return {k: Counter(_norm(k, m.group(0)) for m in p.finditer(text)) for k, p in _PATTERNS.items()}


def diff_fields(left: str, right: str) -> list[FieldChange]:
    lf, rf = extract_fields(left), extract_fields(right)
    out: list[FieldChange] = []
    for kind in _PATTERNS:
        removed = sorted((lf[kind] - rf[kind]).elements())
        added = sorted((rf[kind] - lf[kind]).elements())
        if removed or added:
            out.append(FieldChange(kind=kind, removed=removed, added=added))
    return out


def semantic_hints(left: str, right: str) -> list[str]:
    hints: list[str] = []
    if len(_NEGATIONS.findall(left)) != len(_NEGATIONS.findall(right)):
        hints.append("negation_changed")
    lm = Counter(m.lower() for m in _MODALS.findall(left))
    rm = Counter(m.lower() for m in _MODALS.findall(right))
    if lm != rm:
        hints.append("obligation_changed")
    return hints
