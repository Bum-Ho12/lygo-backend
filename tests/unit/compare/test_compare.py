from pathlib import Path

from app.engine.compare.aligner import compare_documents, similarity
from app.engine.compare.models import ChangeType
from app.ingestion.structure import parse_structure

FIX = Path(__file__).parents[2] / "fixtures"


def _load(name: str):
    return parse_structure((FIX / name).read_text())


def test_identical_documents_are_all_unchanged() -> None:
    d = _load("contract_v1.txt")
    res = compare_documents(d, d)
    assert {i.change for i in res.items} == {ChangeType.unchanged}
    assert res.summary.flagged == 0


def test_contract_versions() -> None:
    res = compare_documents(_load("contract_v1.txt"), _load("contract_v2.txt"))
    by_heading = {i.heading: i for i in res.items}

    pay = by_heading["Payment"]
    assert pay.change == ChangeType.modified
    kinds = {fc.kind: fc for fc in pay.field_changes}
    assert kinds["money"].removed == ["kes 100000"] and kinds["money"].added == ["kes 150000"]
    assert kinds["duration"].removed == ["30 days"] and kinds["duration"].added == ["14 days"]
    assert "numeric_or_date_change" in pay.hints

    term = by_heading["Termination"]  # renumbered 3 -> 4 and 60 -> 90 days
    assert term.change == ChangeType.modified and term.left_number == "3" and term.right_number == "4"
    assert "renumbered" in term.hints and term.field_changes[0].added == ["90 days"]

    assert by_heading["Confidentiality"].change == ChangeType.moved  # only renumbered 4 -> 5
    assert by_heading["Term"].change == ChangeType.added
    assert by_heading["Governing law"].change == ChangeType.removed
    s = res.summary
    assert (s.added, s.removed, s.moved, s.modified) == (1, 1, 1, 2)


def test_removed_item_is_placed_near_its_original_position() -> None:
    res = compare_documents(_load("contract_v1.txt"), _load("contract_v2.txt"))
    headings = [i.heading for i in res.items]
    assert headings.index("Governing law") > headings.index("Confidentiality")


def test_semantic_hints_for_negation_and_obligation() -> None:
    a = parse_structure("1. Duty\nThe supplier shall deliver the goods.")
    b = parse_structure("1. Duty\nThe supplier may not deliver the goods.")
    item = compare_documents(a, b).items[0]
    assert item.change == ChangeType.modified
    assert "negation_changed" in item.hints and "obligation_changed" in item.hints


def test_match_by_similarity_when_headings_differ() -> None:
    a = parse_structure("1. Fees\nThe client shall pay the monthly fee of KES 5,000 to the consultant promptly.")
    b = parse_structure("1. Charges\nThe client shall pay the monthly fee of KES 6,000 to the consultant promptly.")
    items = compare_documents(a, b).items
    assert len(items) == 1 and items[0].change == ChangeType.modified


def test_comparison_is_deterministic_and_similarity_bounds() -> None:
    a, b = _load("contract_v1.txt"), _load("contract_v2.txt")
    assert compare_documents(a, b) == compare_documents(a, b)
    assert 0.0 <= similarity("alpha beta", "gamma delta") <= 1.0 and similarity("x y", "x y") == 1.0
