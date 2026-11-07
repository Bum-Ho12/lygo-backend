from app.engine.compare.documents import diff_fields
from app.ingestion.fields import extract
from app.ingestion.structure import parse_structure

POLICY = """MOTOR INSURANCE POLICY

Policy No: POL/2026/001
Insured: Jane Wanjiru
Insurer: Acme Insurance Ltd

1. Cover. Sum insured KES 2,000,000. Premium KES 40,000 payable within 30 days. Excess 5%.

2. Term. Cover starts on 1 January 2026 and ends on 31 December 2026.
"""


def by(rows, kind):  # type: ignore[no-untyped-def]
    return {(r.key, r.normalized) for r in rows if r.kind == kind}


def test_typed_fields_with_context_labels() -> None:
    fields, _ = extract(parse_structure(POLICY))
    assert ("sum insured", "kes 2000000") in by(fields, "money")
    assert ("premium", "kes 40000") in by(fields, "money")
    assert any(v == "30 days" for _, v in by(fields, "duration"))
    assert ("excess", "5%") in by(fields, "percent")
    assert {v for _, v in by(fields, "date")} == {"1 january 2026", "31 december 2026"}


def test_parties_references_and_entities() -> None:
    fields, ents = extract(parse_structure(POLICY))
    assert by(fields, "party") == {("insured", "jane wanjiru"), ("insurer", "acme insurance ltd")}
    assert by(fields, "reference") == {("policy_number", "pol/2026/001")}
    kinds = {(e.kind, e.name_norm) for e in ents}
    assert ("person", "jane wanjiru") in kinds and ("org", "acme insurance ltd") in kinds and ("reference", "pol 2026 001") in kinds
    span_ok = next(f for f in fields if f.kind == "money")
    assert span_ok.start is not None and span_ok.end is not None and span_ok.end > span_ok.start


def test_between_clause_parties() -> None:
    doc = parse_structure("1. Parties. This agreement is made between Acme Ltd and Beta Co.")
    fields, ents = extract(doc)
    assert by(fields, "party") >= {("party_a", "acme ltd"), ("party_b", "beta co")}
    assert {e.kind for e in ents} == {"org"}


def test_national_ids_are_not_extracted() -> None:
    fields, ents = extract(parse_structure("1. Identity. Insured: Jane Wanjiru, ID No 12345678, KRA PIN A123456789Z."))
    everything = " ".join(f.value + f.normalized for f in fields) + " ".join(e.display for e in ents)
    assert "12345678" not in everything and "A123456789Z".lower() not in everything.lower()


def test_empty_and_noise() -> None:
    assert extract(parse_structure("")) == ([], [])
    assert extract(parse_structure("lorem ipsum dolor sit amet")) == ([], [])


def test_document_field_diff() -> None:
    left = [("money", "sum insured", "kes 2000000"), ("money", "premium", "kes 40000"), ("party", "insured", "jane")]
    right = [("money", "sum insured", "kes 3000000"), ("money", "premium", "kes 40000"), ("date", "expiry", "1 jan 2027")]
    d = {(x.kind, x.key): x for x in diff_fields(left, right)}
    assert d[("money", "sum insured")].status == "changed" and d[("money", "sum insured")].right == ["kes 3000000"]
    assert d[("party", "insured")].status == "only_left" and d[("date", "expiry")].status == "only_right"
    assert ("money", "premium") not in d  # identical fields are omitted
    assert diff_fields([], []) == []
