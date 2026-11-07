"""Deterministic clause alignment and diff between two documents."""
import re
from difflib import SequenceMatcher

from app.engine.compare.fields import diff_fields, semantic_hints
from app.engine.compare.models import (
    ChangeType,
    ComparisonResult,
    ComparisonSummary,
    DiffOp,
    SectionChange,
)
from app.ingestion.models import DocumentText, Section

MATCH_THRESHOLD = 0.6
_WS = re.compile(r"\s+")


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _words(s: str) -> list[str]:
    return _WS.sub(" ", s).strip().split(" ") if s.strip() else []


def similarity(a: str, b: str) -> float:
    wa, wb = _words(_key(a)), _words(_key(b))
    if not wa and not wb:
        return 1.0
    sm = SequenceMatcher(None, wa, wb, autojunk=False)
    if sm.real_quick_ratio() < 0.3 or sm.quick_ratio() < 0.3:
        return sm.quick_ratio()
    return sm.ratio()


def _word_diff(a: str, b: str) -> list[DiffOp]:
    wa, wb = _words(a), _words(b)
    ops: list[DiffOp] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        ops.append(DiffOp(op=tag, left=" ".join(wa[i1:i2]), right=" ".join(wb[j1:j2])))
    return ops


def _align(left: list[Section], right: list[Section]) -> dict[int, int]:
    """Map left index -> right index."""
    pairs: dict[int, int] = {}
    used_r: set[int] = set()

    def take(li: int, ri: int) -> None:
        pairs[li] = ri
        used_r.add(ri)

    # 1. same number and same heading
    for li, l in enumerate(left):
        for ri, r in enumerate(right):
            if ri not in used_r and l.number and l.number == r.number and _key(l.heading) == _key(r.heading):
                take(li, ri)
                break
    # 2. same non-empty heading (renumbered / moved)
    for li, l in enumerate(left):
        if li in pairs or not _key(l.heading):
            continue
        for ri, r in enumerate(right):
            if ri not in used_r and _key(l.heading) == _key(r.heading):
                take(li, ri)
                break
    # 3. best text similarity among the rest, greedy by score
    cands = [
        (similarity(l.full_text, r.full_text), li, ri)
        for li, l in enumerate(left) if li not in pairs
        for ri, r in enumerate(right) if ri not in used_r
    ]
    for score, li, ri in sorted(cands, key=lambda c: (-c[0], c[1], c[2])):
        if score >= MATCH_THRESHOLD and li not in pairs and ri not in used_r:
            take(li, ri)
    return pairs


def compare_documents(left: DocumentText, right: DocumentText) -> ComparisonResult:
    pairs = _align(left.sections, right.sections)
    rev = {r: l for l, r in pairs.items()}
    entries: list[tuple[float, SectionChange]] = []

    for ri, r in enumerate(right.sections):
        if ri not in rev:
            entries.append((float(ri), SectionChange(
                change=ChangeType.added, right_path=r.path, right_number=r.number, heading=r.heading,
                similarity=0.0, field_changes=diff_fields("", r.full_text),
            )))
            continue
        l = left.sections[rev[ri]]
        same_text = _key(l.full_text) == _key(r.full_text)
        renumbered = bool(l.number or r.number) and l.number != r.number
        sim = 1.0 if same_text else similarity(l.full_text, r.full_text)
        hints: list[str]
        if same_text and not renumbered:
            change, hints = ChangeType.unchanged, []
        elif same_text:
            change, hints = ChangeType.moved, []
        else:
            change = ChangeType.modified
            hints = semantic_hints(l.full_text, r.full_text)
        fields = [] if same_text else diff_fields(l.full_text, r.full_text)
        if fields:
            hints = ["numeric_or_date_change", *hints]
        if renumbered and change == ChangeType.modified:
            hints.append("renumbered")
        entries.append((float(ri), SectionChange(
            change=change, left_path=l.path, right_path=r.path, left_number=l.number,
            right_number=r.number, heading=r.heading or l.heading, similarity=round(sim, 4),
            diff=[] if same_text else _word_diff(l.full_text, r.full_text),
            field_changes=fields, hints=hints,
        )))

    # place removed sections right after the nearest preceding matched left section
    for li, l in enumerate(left.sections):
        if li in pairs:
            continue
        prev = max((pairs[j] for j in range(li) if j in pairs), default=-1)
        entries.append((prev + 0.5, SectionChange(
            change=ChangeType.removed, left_path=l.path, left_number=l.number, heading=l.heading,
            similarity=0.0, field_changes=diff_fields(l.full_text, ""),
        )))

    entries.sort(key=lambda e: e[0])
    items = [c for _, c in entries]
    summary = ComparisonSummary()
    for c in items:
        setattr(summary, c.change.value, getattr(summary, c.change.value) + 1)
        if c.hints or (c.change in (ChangeType.added, ChangeType.removed) and c.field_changes):
            summary.flagged += 1
    return ComparisonResult(items=items, summary=summary)
