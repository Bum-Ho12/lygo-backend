"""Typed field and entity extraction (rules-first; model fallback comes with the AI engine).

Extracts amounts, periods, percentages, dates, parties and reference numbers, each with a context label so the same
quantity ('sum insured', 'premium') can be compared across documents. National ID numbers are intentionally NOT
extracted (data minimisation; docs/07 requires field-level encryption before we store them).
"""
import re
from dataclasses import dataclass

from app.engine.compare.fields import find_values
from app.ingestion.models import DocumentText

_STOP = {"of", "is", "are", "the", "a", "an", "to", "in", "on", "at", "by", "for", "and", "or", "kes", "usd", "ksh", "within", "than", "be", "will", "shall", "pay", "paid"}
_ROLES = "insured|insurer|landlord|tenant|lessor|lessee|employer|employee|client|contractor|supplier|buyer|seller|plaintiff|defendant|petitioner|respondent|claimant|donor|donee|borrower|lender"
_PARTY_LABEL = re.compile(rf"\b({_ROLES})\s*[:\-]\s*([A-Z][A-Za-z0-9&.' -]{{2,80}}?)(?=[.,;\n]|\s{{2,}}|$)", re.IGNORECASE)
_BETWEEN = re.compile(r"\bbetween\s+([A-Z][A-Za-z0-9&.' -]{2,80}?)\s+(?:\([^)]*\)\s*)?and\s+([A-Z][A-Za-z0-9&.' -]{2,80}?)(?=[,.(;\n]|$)")
_REFS = {
    "policy_number": re.compile(r"\bpolic(?:y|ies)\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,30})", re.IGNORECASE),
    "claim_number": re.compile(r"\bclaim\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,30})", re.IGNORECASE),
    "case_number": re.compile(r"\b(?:case|suit|petition|appeal)\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,30})", re.IGNORECASE),
    "permit_number": re.compile(r"\bpermit\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,30})", re.IGNORECASE),
    "invoice_number": re.compile(r"\binvoice\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,30})", re.IGNORECASE),
}
_ORG = re.compile(r"\b(ltd|limited|co|company|inc|llp|plc|sacco|bank|insurance|assurance|holdings|enterprises|trust|foundation|council|ministry)\b\.?", re.IGNORECASE)


@dataclass(frozen=True)
class FieldRow:
    kind: str  # money | duration | percent | date | party | reference
    key: str  # context label ('sum insured'), party role ('insured') or reference type ('policy_number')
    value: str
    normalized: str
    section_path: str = ""
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class EntityRow:
    kind: str  # person | org | reference
    name_norm: str
    display: str
    role: str
    section_path: str


def _label(text: str, start: int) -> str:
    """Up to three meaningful words immediately before a value, on the same line."""
    before = text[max(0, start - 60) : start].splitlines()[-1] if text[:start] else ""
    before = re.split(r"[.;:!?]\s", before)[-1]  # a label never crosses a sentence boundary
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]*", before)][-4:]
    kept = [w.lower() for w in words if w.lower() not in _STOP][-3:]
    return " ".join(kept) or "unlabelled"


def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _entity_kind(name: str) -> str:
    return "org" if _ORG.search(name) else "person"


def extract(doc: DocumentText) -> tuple[list[FieldRow], list[EntityRow]]:
    fields: list[FieldRow] = []
    entities: dict[tuple[str, str, str], EntityRow] = {}

    def add_entity(kind: str, display: str, role: str, path: str) -> None:
        display = " ".join(display.split()).strip(" .,'-")
        if len(display) < 3 or display.lower() in _ROLES.split("|"):
            return
        entities.setdefault((kind, norm_name(display), role), EntityRow(kind, norm_name(display), display[:200], role, path))

    for s in doc.sections:
        text = s.full_text
        for kind, raw, normalized, start, end in find_values(text):
            fields.append(FieldRow(kind, _label(text, start), raw.strip(), normalized, s.path, start, end))
        for m in _PARTY_LABEL.finditer(text):
            role, name = m.group(1).lower(), " ".join(m.group(2).split()).strip(" .,'-")
            fields.append(FieldRow("party", role, name, norm_name(name), s.path, m.start(2), m.end(2)))
            add_entity(_entity_kind(name), name, role, s.path)
        for m in _BETWEEN.finditer(text):
            for idx, role in ((1, "party_a"), (2, "party_b")):
                name = " ".join(m.group(idx).split()).strip(" .,'-")
                fields.append(FieldRow("party", role, name, norm_name(name), s.path, m.start(idx), m.end(idx)))
                add_entity(_entity_kind(name), name, role, s.path)
        for key, pat in _REFS.items():
            for m in pat.finditer(text):
                ref = m.group(1).upper()
                fields.append(FieldRow("reference", key, ref, ref.lower(), s.path, m.start(1), m.end(1)))
                add_entity("reference", ref, key, s.path)
    return fields, list(entities.values())
