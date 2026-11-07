"""Word automatic list numbering is reconstructed during extraction (judgments are cited by paragraph number)."""
import io
from pathlib import Path

import docx
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from app.ingestion.parsers.extract import _fmt_number, extract
from tests.unit.ingestion import factories as fx


def _save(d, tmp_path: Path) -> Path:  # type: ignore[no-untyped-def]
    p = tmp_path / "n.docx"
    buf = io.BytesIO()
    d.save(buf)
    p.write_bytes(buf.getvalue())
    return p


def _lines(path: Path) -> list[str]:
    return [ln for ln in extract("docx", path, fx.FakeOcr()).pages[0].text.splitlines() if ln.strip()]


def _custom_list(d, abs_id: str, num_id: str, start_override: int | None = None) -> None:  # type: ignore[no-untyped-def]
    """Three-level list: '1.', '(a)', '(iii)'."""
    lvls = "".join(
        f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text}"/></w:lvl>'
        for i, (fmt, text) in enumerate([("decimal", "%1."), ("lowerLetter", "(%2)"), ("lowerRoman", "(%3)")])
    )
    numbering = d.part.numbering_part.element
    numbering.insert(0, parse_xml(f'<w:abstractNum {nsdecls("w")} w:abstractNumId="{abs_id}">{lvls}</w:abstractNum>'))
    override = (f'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="{start_override}"/></w:lvlOverride>'
                if start_override else "")
    numbering.append(parse_xml(f'<w:num {nsdecls("w")} w:numId="{num_id}"><w:abstractNumId w:val="{abs_id}"/>{override}</w:num>'))


def _numbered(d, text: str, num_id: str, ilvl: int = 0):  # type: ignore[no-untyped-def]
    p = d.add_paragraph(text)
    p._p.get_or_add_pPr().append(parse_xml(
        f'<w:numPr {nsdecls("w")}><w:ilvl w:val="{ilvl}"/><w:numId w:val="{num_id}"/></w:numPr>'))
    return p


def test_list_number_style_paragraphs_get_their_numbers(tmp_path: Path) -> None:
    d = docx.Document()
    d.add_paragraph("JUDGMENT")
    for t in ("The Plaintiff sued.", "The Defendant denied.", "Judgment for the Plaintiff."):
        d.add_paragraph(t, style="List Number")
    assert _lines(_save(d, tmp_path)) == ["JUDGMENT", "1. The Plaintiff sued.", "2. The Defendant denied.",
                                          "3. Judgment for the Plaintiff."]


def test_empty_numbered_paragraph_still_advances_the_counter(tmp_path: Path) -> None:
    d = docx.Document()
    d.add_paragraph("First.", style="List Number")
    d.add_paragraph("", style="List Number")  # Word shows "2." on this empty line
    d.add_paragraph("Third.", style="List Number")
    assert _lines(_save(d, tmp_path)) == ["1. First.", "3. Third."]


def test_multilevel_formats_and_restart_of_lower_levels(tmp_path: Path) -> None:
    d = docx.Document()
    _custom_list(d, "90", "90")
    _numbered(d, "Issues.", "90")
    _numbered(d, "Notice.", "90", 1)
    _numbered(d, "Disclosure.", "90", 1)
    _numbered(d, "Sub-point.", "90", 2)
    _numbered(d, "Orders.", "90")
    _numbered(d, "Costs.", "90", 1)
    assert _lines(_save(d, tmp_path)) == ["1. Issues.", "(a) Notice.", "(b) Disclosure.", "(i) Sub-point.",
                                          "2. Orders.", "(a) Costs."]


def test_start_override_and_numid_zero(tmp_path: Path) -> None:
    d = docx.Document()
    _custom_list(d, "91", "91", start_override=5)
    _numbered(d, "Continues at five.", "91")
    _numbered(d, "Six.", "91")
    off = d.add_paragraph("Not numbered.", style="List Number")
    off._p.get_or_add_pPr().append(parse_xml(f'<w:numPr {nsdecls("w")}><w:numId w:val="0"/></w:numPr>'))
    assert _lines(_save(d, tmp_path)) == ["5. Continues at five.", "6. Six.", "Not numbered."]


def test_plain_and_typed_numbers_are_untouched(tmp_path: Path) -> None:
    p = tmp_path / "t.docx"
    p.write_bytes(fx.docx_bytes("1. Typed number", "Plain text"))
    assert _lines(p) == ["1. Typed number", "Plain text"]


def test_number_formats() -> None:
    assert [_fmt_number(n, "lowerRoman") for n in (1, 4, 9, 14, 40)] == ["i", "iv", "ix", "xiv", "xl"]
    assert [_fmt_number(n, "upperLetter") for n in (1, 26, 27)] == ["A", "Z", "AA"]
    assert _fmt_number(7, "decimalZero") == "07" and _fmt_number(3, "none") == "" and _fmt_number(3, "ordinal") == "3"
