"""Matter chronology: every dated event the file contains, in order, each traceable to its source. Deterministic (no LLM)."""
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.documents import Document, DocumentField, DocumentSection
from app.db.models.tenancy import Matter
from app.db.models.work import Deadline

_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], start=1)}
_DMY = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\s+(\d{4})$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
SNIPPET_RADIUS = 110


@dataclass
class Event:
    date: date
    kind: str  # document | deadline | matter
    title: str
    snippet: str = ""
    document_id: uuid.UUID | None = None
    section_path: str = ""
    page: int | None = None
    status: str = ""


def parse_date(value: str) -> date | None:
    v = " ".join(value.lower().split())
    try:
        if m := _ISO.match(v):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if (m := _DMY.match(v)) and m[2] in _MONTHS:
            return date(int(m[3]), _MONTHS[m[2]], int(m[1]))
    except ValueError:  # e.g. 31 February
        return None
    return None


def _snippet(full_text: str, start: int | None, end: int | None) -> str:
    if start is None or end is None:
        return " ".join(full_text.split())[: SNIPPET_RADIUS * 2]
    a, b = max(0, start - SNIPPET_RADIUS), min(len(full_text), end + SNIPPET_RADIUS)
    return ("…" if a else "") + " ".join(full_text[a:b].split()) + ("…" if b < len(full_text) else "")


async def build_chronology(db: AsyncSession, matter: Matter) -> list[Event]:
    events: list[Event] = [Event(matter.opened_at.astimezone(UTC).date(), "matter", f"Matter opened: {matter.title}")]
    docs = {d.id: d for d in (await db.execute(select(Document).where(Document.matter_id == matter.id, Document.deleted_at.is_(None)))).scalars()}
    if docs:
        sections = {(s.document_id, s.path): s for s in (await db.execute(select(DocumentSection).where(DocumentSection.document_id.in_(docs)))).scalars()}
        fields = (await db.execute(select(DocumentField).where(DocumentField.document_id.in_(docs), DocumentField.kind == "date").order_by(DocumentField.id))).scalars()
        seen: set[tuple[date, uuid.UUID, str, int | None]] = set()
        for f in fields:
            d = parse_date(f.normalized)
            key = (d, f.document_id, f.section_path, f.span_start) if d else None
            if d is None or key in seen:
                continue
            seen.add(key)  # type: ignore[arg-type]
            sec = sections.get((f.document_id, f.section_path))
            full = f"{sec.heading}\n{sec.text}".strip() if sec else ""  # same layout the extractor measured offsets against
            events.append(Event(d, "document", docs[f.document_id].title, _snippet(full, f.span_start, f.span_end), f.document_id, f.section_path, sec.page if sec else None))
    for dl in (await db.execute(select(Deadline).where(Deadline.matter_id == matter.id))).scalars():
        events.append(Event(dl.due_at.astimezone(UTC).date(), "deadline", dl.title, status=dl.status))
    order = {"matter": 0, "document": 1, "deadline": 2}
    return sorted(events, key=lambda e: (e.date, order[e.kind], e.title))


_ = datetime
