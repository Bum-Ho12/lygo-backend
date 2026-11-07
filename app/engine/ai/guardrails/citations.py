"""Citation verification (docs/04 guardrail 1): the model may only state what the supplied sources support.

Rules, applied sentence by sentence (formatting is preserved line by line):
  * a [S#] marker that does not map to a supplied source is invalid -> the sentence is removed;
  * a statutory section ('section 12', 's.12'), case name or report citation that does not appear in the supplied sources -> removed;
  * a substantive sentence (>= 8 words) with no citation -> removed, EXCEPT sentences that assert nothing about the law:
    headings, questions, statements about what the sources do NOT cover, and "check/confirm/consider..." reminders. Those
    still go through the invalid-marker and invented-authority checks above;
  * if nothing verifiably cited remains, the answer is not ok and the caller must abstain.

Not checked: whether a cited sentence is actually *entailed* by its source (see docs/14, Phase E2 status).
"""
import re
from dataclasses import dataclass, field

MARKER = re.compile(r"\[S(\d+)\]")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(\d\"'*_])")
_SECTION = re.compile(r"\b(?:sections?|sec\.|s\.|ss\.)\s*(\d+[A-Z]?)(?:\s*\(\d+\))?", re.IGNORECASE)
_CASE = re.compile(r"\b([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})\s+v(?:s)?\.?\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})")
_REPORT = re.compile(r"\[\d{4}\]\s*(?:e)?KLR|\(\d{4}\)\s*eKLR", re.IGNORECASE)
_TRAILING_MARKERS = re.compile(r"([.!?])\s*((?:\[S\d+\]\s*)+)")
_PREFIX = re.compile(r"^(\s*(?:[-*•]\s+|\d+[.)]\s+|>\s+)?(?:\*\*[^*\n]{1,80}\*\*:?\s+)?)(.*)$")  # list marker + optional bold label
_HEADING = re.compile(r"^\s*(?:#{1,6}\s|\*\*[^*\n]+\*\*:?\s*$)")
_EMPHASIS_ONLY = re.compile(r"^\s*(?:\*[^*\n].*\*|_[^_\n].*_)\s*$")
_META = re.compile(
    r"\b(?:sources?|excerpts?|extracts?|materials?)\s+(?:do(?:es)?\s+not|don't|contain(?:s)?\s+no|provide(?:s)?\s+no|set(?:s)?\s+no|"
    r"is\s+silent|are\s+silent)\b|\bnot\s+(?:addressed|covered|defined|specified|stated|mentioned)\s+in\s+the\s+(?:sources?|excerpts?|materials?)\b",
    re.IGNORECASE,
)
_REMINDER = re.compile(r"^\W*(?:check|confirm|consider|research|verify|review|ask|obtain|seek|compare|clarify|ensure|assess|examine)\b", re.IGNORECASE)
MIN_UNCITED_WORDS = 8


@dataclass(frozen=True)
class Source:
    marker: int  # 1-based, as shown to the model
    chunk_id: str
    title: str
    section_ref: str
    text: str


@dataclass
class Removed:
    sentence: str
    reason: str  # invalid_marker | unsupported_authority | uncited


@dataclass
class Verified:
    text: str
    used: list[int] = field(default_factory=list)  # source markers actually cited, in order of first use
    removed: list[Removed] = field(default_factory=list)
    kept_sentences: int = 0
    ok: bool = False


def _normalise(text: str) -> str:
    """Models often put the marker after the full stop ("... days. [S1]"); attach it to the sentence it supports."""

    def move(m: re.Match[str]) -> str:
        markers = " ".join(f"[S{n}]" for n in MARKER.findall(m.group(2)))
        return f" {markers}{m.group(1)} "

    return _TRAILING_MARKERS.sub(move, text)


def _authorities_supported(sentence: str, sources: list[Source], cited: list[int]) -> bool:
    haystack_all = " ".join(f"{s.section_ref} {s.text}" for s in sources).lower()
    haystack_cited = " ".join(f"{s.section_ref} {s.text}" for s in sources if s.marker in cited).lower() or haystack_all
    for m in _SECTION.finditer(sentence):
        num = m.group(1).lower()
        if not re.search(rf"(?:\bs\.\s?|\bsections?\s+|\bclause\s+){re.escape(num)}\b|\b{re.escape(num)}\.\s", haystack_cited):
            return False
    for m in _CASE.finditer(sentence):
        # the capture may include a leading sentence word ("See Smith"), so match on the first party's last word
        first_party_tail = m.group(1).split()[-1].lower()
        if not re.search(rf"\b{re.escape(first_party_tail)}\s+v(?:s)?\.?\s", haystack_all):
            return False
    if _REPORT.search(sentence):
        return bool(_REPORT.search(haystack_all))
    return True


def _asserts_nothing(sentence: str) -> bool:
    s = sentence.strip()
    return s.endswith("?") or bool(_META.search(s)) or bool(_REMINDER.match(s))


def verify(answer: str, sources: list[Source]) -> Verified:
    valid = {s.marker for s in sources}
    out = Verified(text="")
    lines: list[str] = []
    for raw in answer.splitlines():
        if not raw.strip():
            if lines and lines[-1] != "":
                lines.append("")
            continue
        prefix, body = _PREFIX.match(raw).groups()  # type: ignore[union-attr]
        structural = bool(_HEADING.match(raw) or _EMPHASIS_ONLY.match(raw))
        kept: list[str] = []
        for sentence in (s.strip() for s in _SENT.split(_normalise(body))):
            if not sentence:
                continue
            markers = [int(m) for m in MARKER.findall(sentence)]
            if any(n not in valid for n in markers):
                out.removed.append(Removed(sentence, "invalid_marker"))
                continue
            if not _authorities_supported(sentence, sources, markers):
                out.removed.append(Removed(sentence, "unsupported_authority"))
                continue
            if not markers and not structural and not _asserts_nothing(sentence) and len(re.findall(r"\w+", sentence)) >= MIN_UNCITED_WORDS:
                out.removed.append(Removed(sentence, "uncited"))
                continue
            for n in markers:
                if n not in out.used:
                    out.used.append(n)
            kept.append(sentence)
        if kept:
            out.kept_sentences += len(kept)
            lines.append(prefix + " ".join(kept))
    out.text = "\n".join(lines).strip()
    out.ok = bool(out.used) and bool(out.text)
    return out
