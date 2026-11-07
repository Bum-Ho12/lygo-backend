"""Cite-check (deterministic): do the authorities a draft cites exist in the sources Lygo holds?

IMPORTANT wording: "not found" means NOT IN THE SOURCES AVAILABLE TO LYGO. The public corpus is incomplete, so a miss is a
prompt to verify, never proof that an authority is fabricated. Verdicts:
    verified      the cited section (and Act, if named) exists in the loaded sources in force on the as-at date
    not_found     no such section/case in the loaded sources
    ambiguous     the section number exists in several Acts and the draft does not say which
"""
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ACT = r"(?P<act>[A-Z][A-Za-z&',\- ]{2,80}?(?:Act|Constitution|Regulations|Rules|Code))(?:,?\s*(?:Cap\.?\s*\d+[A-Z]?|\d{4}))?"
_STATUTE = re.compile(
    rf"\b(?:sections?|sec\.|s\.|ss\.)\s*(?P<num>\d+[A-Z]?)(?:\s*\((?P<sub>\w+)\))*(?:\s+of\s+(?:the\s+)?{_ACT})?", re.IGNORECASE,
)
_CASE = re.compile(r"\b(?P<a>[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})\s+v(?:s)?\.?\s+(?P<b>[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})")
_REPORT = re.compile(r"\[\d{4}\]\s*(?:e)?KLR(?:\s*\w+)?|\(\d{4}\)\s*eKLR", re.IGNORECASE)
_LEADING_JUNK = {"see", "in", "per", "the", "also", "cf", "compare", "under", "and", "but", "as", "held", "court"}


@dataclass
class Reference:
    kind: str  # statute | case | report
    text: str
    section: str = ""
    act: str = ""
    parties: tuple[str, str] = ("", "")


@dataclass
class CiteResult:
    reference: Reference
    verdict: str
    detail: dict[str, object] = field(default_factory=dict)


def extract_references(draft: str) -> list[Reference]:
    refs: list[Reference] = []
    seen: set[tuple[str, str, str]] = set()
    for m in _STATUTE.finditer(draft):
        act = (m.group("act") or "").strip(" ,")
        act = re.sub(r"^(?:the\s+)", "", act, flags=re.IGNORECASE)
        key = ("statute", m.group("num").lower(), act.lower())
        if key not in seen:
            seen.add(key)
            refs.append(Reference("statute", m.group(0).strip(), section=m.group("num"), act=act))
    for m in _CASE.finditer(draft):
        a_words = [w for w in m.group("a").split() if w.lower().strip(".,") not in _LEADING_JUNK]
        a = " ".join(a_words)
        if not a:
            continue
        key = ("case", a.lower(), m.group("b").lower())
        if key not in seen:
            seen.add(key)
            refs.append(Reference("case", f"{a} v {m.group('b')}", parties=(a, m.group("b"))))
    for m in _REPORT.finditer(draft):
        key = ("report", m.group(0).lower(), "")
        if key not in seen:
            seen.add(key)
            refs.append(Reference("report", m.group(0)))
    return refs


async def check_references(db: AsyncSession, refs: list[Reference], as_at: date) -> list[CiteResult]:
    out: list[CiteResult] = []
    for r in refs:
        if r.kind == "statute":
            out.append(await _check_statute(db, r, as_at))
        else:
            out.append(await _check_text_reference(db, r))
    return out


async def _check_statute(db: AsyncSession, r: Reference, as_at: date) -> CiteResult:
    ref = f"s.{r.section.lower()}"
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT ON (source_id) source_id, title, section_ref, version_label, left(text, 400) AS snippet FROM chunks "
                "WHERE scope = 'public' AND (lower(section_ref) = :ref OR lower(section_ref) LIKE :refp) "
                "AND (effective_from IS NULL OR effective_from <= :d) AND (effective_to IS NULL OR effective_to > :d) "
                "AND (CAST(:act AS text) IS NULL OR title ILIKE '%' || :act || '%') ORDER BY source_id, ord LIMIT 5"
            ),
            {"ref": ref, "refp": ref + " (%", "d": as_at, "act": r.act or None},
        )
    ).all()
    if not rows:
        return CiteResult(r, "not_found", {"note": "No such section in the sources available to Lygo" + (f" for '{r.act}'" if r.act else "") + ". Verify against the official text."})
    if len(rows) > 1 and not r.act:
        return CiteResult(r, "ambiguous", {"candidates": [{"title": x.title, "section": x.section_ref, "version": x.version_label} for x in rows], "note": "The section number exists in several Acts; name the Act."})
    x = rows[0]
    return CiteResult(r, "verified", {"title": x.title, "section": x.section_ref, "version": x.version_label, "snippet": x.snippet, "as_at": as_at.isoformat()})


async def _check_text_reference(db: AsyncSession, r: Reference) -> CiteResult:
    """Cases / law-report citations: present only if their text appears in a source the caller can see (public corpus or firm KB)."""
    needle = f"{r.parties[0].split()[-1]} v" if r.kind == "case" else r.text
    rows = (
        await db.execute(
            text("SELECT title, section_ref, scope FROM chunks WHERE scope IN ('public', 'firm_kb') AND text ILIKE '%' || :n || '%' LIMIT 3"),
            {"n": needle},
        )
    ).all()
    if rows:
        return CiteResult(r, "verified", {"found_in": [{"title": x.title, "section": x.section_ref, "scope": x.scope} for x in rows]})
    return CiteResult(r, "not_found", {"note": "Not found in the sources available to Lygo (the case-law corpus is not loaded). Verify against the law report."})
