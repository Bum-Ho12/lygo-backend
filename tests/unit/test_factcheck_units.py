import pytest

from app.domain.matters.chronology import parse_date
from app.engine.ai.guardrails.citations import Source
from app.engine.ai.json_utils import NotJson, extract_json
from app.engine.ai.providers.fake import ScriptedLLM
from app.engine.factcheck.citations import extract_references
from app.engine.factcheck.claims import (
    ExtractedClaim,
    Judgement,
    extract_claims,
    judge_claims,
    verify_judgement,
)

pytestmark = pytest.mark.asyncio
SRC = [
    Source(1, "c1", "Repair estimate", "clause 2", "The vehicle was damaged on 14 March 2026. The claimed amount is KES 450,000 including labour."),
    Source(2, "c2", "Police abstract", "clause 1", "Reported at Kilimani police station on 15 March 2026."),
]


# --- reference extraction ---------------------------------------------------------------------------------------------------
def test_statute_case_and_report_references_are_extracted() -> None:
    refs = extract_references(
        "Under section 5 of the Insurance Act, Cap. 487 the claimant must notify. See also s.12(3) and Section 7A of the Employment Act, 2007. "
        "The court followed Kamau v Nation Media Ltd [2019] eKLR."
    )
    statutes = [(r.section, r.act) for r in refs if r.kind == "statute"]
    assert ("5", "Insurance Act") in statutes and ("12", "") in statutes and ("7A", "Employment Act") in statutes
    assert [r.text for r in refs if r.kind == "case"] == ["Kamau v Nation Media Ltd"]
    assert [r.text for r in refs if r.kind == "report"] == ["[2019] eKLR"]


def test_references_are_deduplicated_and_noise_is_ignored() -> None:
    assert len([r for r in extract_references("section 5 applies. Section 5 applies again. s.5 too.") if r.kind == "statute"]) == 1
    assert extract_references("no authorities here, just prose about a car crash in March") == []
    [case] = [r for r in extract_references("In Smith v Jones the court held otherwise.") if r.kind == "case"]
    assert case.parties[0] == "Smith"  # leading words like "In" are not part of the party name


# --- JSON + dates ---------------------------------------------------------------------------------------------------------------
def test_json_extraction_is_tolerant_but_not_gullible() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure! Here you go:\n```json\n{"a": [1, 2]}\n```\nHope that helps') == {"a": [1, 2]}
    assert extract_json('prefix {"a": 1} suffix') == {"a": 1}
    with pytest.raises(NotJson):
        extract_json("I could not find any claims.")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("14 march 2026", (2026, 3, 14)), ("1st january 2026", (2026, 1, 1)), ("2026-11-07", (2026, 11, 7)), ("22nd  september 2025", (2025, 9, 22))],
)
def test_parse_date(raw: str, expected: tuple[int, int, int]) -> None:
    d = parse_date(raw)
    assert d is not None and (d.year, d.month, d.day) == expected


@pytest.mark.parametrize("raw", ["31 february 2026", "2026-13-01", "5 smarch 2026", "march 2026", ""])
def test_parse_date_rejects_impossible_dates(raw: str) -> None:
    assert parse_date(raw) is None


# --- claim verification: the model proposes, code disposes -------------------------------------------------------------------
def claim(text: str, i: int = 1) -> ExtractedClaim:
    return ExtractedClaim(id=i, claim=text)


def judged(verdict: str, *evidence: tuple[int, str]) -> Judgement:
    return Judgement(id=1, verdict=verdict, evidence=[{"marker": m, "quote": q} for m, q in evidence])  # type: ignore[arg-type]


def test_a_supported_claim_needs_a_verbatim_quote() -> None:
    ok = verify_judgement(claim("The vehicle was damaged on 14 March 2026"), judged("supported", (1, "The vehicle was damaged on 14 March 2026.")), SRC)
    assert ok.verdict == "supported" and ok.evidence[0]["document"] == "Repair estimate" and not ok.downgraded
    # whitespace/case/quote-mark differences in the quote are tolerated, paraphrase is not
    assert verify_judgement(claim("Damaged on 14 March 2026"), judged("supported", (1, "the vehicle  was DAMAGED on 14 March 2026")), SRC).verdict == "supported"
    fake = verify_judgement(claim("The driver was drunk at the time of the crash"), judged("supported", (1, "The driver was intoxicated")), SRC)
    assert fake.verdict == "not_found" and fake.downgraded == "unverifiable_evidence"  # invented evidence never yields a verdict


def test_wrong_marker_tiny_quote_and_missing_verdict_are_rejected() -> None:
    assert verify_judgement(claim("x damaged"), judged("supported", (9, "The vehicle was damaged on 14 March 2026")), SRC).verdict == "not_found"  # no such source
    assert verify_judgement(claim("something happened"), judged("supported", (2, "The vehicle was damaged on 14 March 2026")), SRC).verdict == "not_found"  # quote is from another source
    assert verify_judgement(claim("something happened"), judged("supported", (1, "damaged")), SRC).verdict == "not_found"  # too short to prove anything
    assert verify_judgement(claim("something happened"), None, SRC).downgraded == "no_verdict"
    assert verify_judgement(claim("something happened"), judged("not_found"), SRC).verdict == "not_found"


def test_numbers_and_dates_in_the_claim_must_appear_in_the_evidence() -> None:
    wrong_amount = verify_judgement(claim("The claimed amount is KES 500,000"), judged("supported", (1, "The claimed amount is KES 450,000 including labour")), SRC)
    assert wrong_amount.verdict == "partially_supported" and "500000" in wrong_amount.downgraded  # the model said 'supported'; the figures disagree
    right = verify_judgement(claim("The claimed amount is KES 450,000"), judged("supported", (1, "The claimed amount is KES 450,000 including labour")), SRC)
    assert right.verdict == "supported"
    # contradictions keep their verdict: they are SUPPOSED to disagree with the claim, but still need real evidence
    contradicted = verify_judgement(claim("Damaged on 20 March 2026"), judged("contradicted", (1, "The vehicle was damaged on 14 March 2026")), SRC)
    assert contradicted.verdict == "contradicted" and not contradicted.downgraded
    assert verify_judgement(claim("Damaged on 20 March 2026"), judged("contradicted", (1, "The vehicle was never damaged at all")), SRC).verdict == "not_found"


async def test_extraction_drops_claims_not_traceable_to_the_draft_and_retries_bad_json() -> None:
    draft = "The vehicle was damaged on 14 March 2026. The insured paid KES 20,000 excess."
    llm = ScriptedLLM(
        "sorry, I cannot produce JSON",  # first attempt unusable
        '{"claims":[{"id":1,"claim":"Vehicle damaged 14 March 2026","quote":"The vehicle was damaged on 14 March 2026"},'
        '{"id":2,"claim":"The insured was driving drunk","quote":"The insured was driving drunk"}]}',  # #2 is invented
    )
    got = await extract_claims(llm, draft)
    assert [c.id for c in got] == [1] and len(llm.calls) == 2
    with pytest.raises(ValueError):
        await extract_claims(ScriptedLLM("nope", "still nope"), draft)


async def test_judging_with_no_sources_or_unusable_output_never_invents_verdicts() -> None:
    cs = [claim("a fact about something", 1)]
    assert [r.verdict for r in await judge_claims(ScriptedLLM(), cs, [])] == ["not_found"]  # nothing on file to check against
    out = await judge_claims(ScriptedLLM("garbage", "garbage"), cs, SRC)
    assert out[0].verdict == "not_found" and out[0].downgraded == "no_verdict"
    assert await judge_claims(ScriptedLLM(), [], SRC) == []
