from pathlib import Path

from app.ingestion.structure import parse_structure

FIX = Path(__file__).parents[2] / "fixtures"


def test_parses_numbered_clauses_with_headings() -> None:
    doc = parse_structure((FIX / "contract_v1.txt").read_text())
    nums = [s.number for s in doc.sections]
    assert nums == ["", "1", "2", "3", "4", "5"]  # first is the title/preamble block
    term = next(s for s in doc.sections if s.number == "3")
    assert term.heading == "Termination" and "60 days" in term.text


def test_subclauses_get_tree_paths() -> None:
    doc = parse_structure("1. General\nintro\n1.1 Scope\nscope text\n1.2 Term\nterm text\n2. Pay\nx")
    assert [s.path for s in doc.sections] == ["1", "1/1.1", "1/1.2", "2"]


def test_duplicate_numbers_get_unique_paths() -> None:
    doc = parse_structure("1. A\nx\n1. A again\ny")
    assert len({s.path for s in doc.sections}) == 2


def test_empty_text() -> None:
    assert parse_structure("   \n\n").sections == []
