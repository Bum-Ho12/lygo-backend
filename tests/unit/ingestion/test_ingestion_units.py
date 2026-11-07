import asyncio
import io

import pymupdf
import pytest

from app.ingestion.classify.classifier import classify
from app.ingestion.parsers.extract import extract
from app.ingestion.scan import EICAR, ClamdScanner, EicarOnlyScanner, ScannerUnavailable
from app.ingestion.sniff import UnsupportedFile, sniff
from tests.unit.ingestion import factories as fx

pytestmark = pytest.mark.asyncio


# --- sniff ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("data", "name", "kind"),
    [
        (fx.pdf_text("hello world, this is a pdf"), "a.pdf", "pdf"),
        (fx.docx_bytes("hello"), "a.docx", "docx"),
        (fx.png_bytes(), "scan.png", "image"),
        (b"col1,col2\n1,2\n", "data.csv", "text"),
        ("plain ünicode text".encode(), "n.txt", "text"),
    ],
)
def test_sniff_accepts_and_classifies(data: bytes, name: str, kind: str) -> None:
    assert sniff(io.BytesIO(data), name).kind == kind


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (fx.png_bytes(), "fake.pdf"),  # content/extension mismatch
        (fx.pdf_text("hello world, this is a pdf"), "a.docx"),
        (b"MZ\x90\x00\x03\x00\x00\x00binary", "evil.exe"),  # unsupported extension
        (b"\x00\x01\x02\xff binary", "blob.txt"),  # binary masquerading as text
        (b"PK\x03\x04" + b"\x00" * 30, "a.docx"),  # corrupt zip
    ],
)
def test_sniff_rejects(data: bytes, name: str) -> None:
    with pytest.raises(UnsupportedFile):
        sniff(io.BytesIO(data), name)


def test_sniff_rejects_arbitrary_zip_even_if_named_docx() -> None:
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("payload.bin", b"x")
    with pytest.raises(UnsupportedFile):
        sniff(io.BytesIO(buf.getvalue()), "a.docx")


# --- scanners ------------------------------------------------------------------------------------
async def test_eicar_scanner() -> None:
    s = EicarOnlyScanner()
    assert (await s.scan(io.BytesIO(b"harmless" * 1000))).clean
    r = await s.scan(io.BytesIO(b"x" * (1 << 20) + EICAR + b"y"))  # straddles chunk boundaries
    assert not r.clean and r.signature == "Eicar-Test-Signature"


async def _clamd(reply: bytes):  # type: ignore[no-untyped-def]
    handler = fx.clamd_reply(reply)
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, handler, server.sockets[0].getsockname()[1]


async def test_clamd_scanner_protocol() -> None:
    payload = b"a" * 200_000
    server, handler, port = await _clamd(b"stream: OK")
    async with server:
        assert (await ClamdScanner("127.0.0.1", port).scan(io.BytesIO(payload))).clean
        assert handler.received == payload  # framed correctly and complete
    server, _, port = await _clamd(b"stream: Win.Test.EICAR_HDB-1 FOUND")
    async with server:
        r = await ClamdScanner("127.0.0.1", port).scan(io.BytesIO(b"x"))
        assert not r.clean and r.signature == "Win.Test.EICAR_HDB-1"


async def test_clamd_fails_closed() -> None:
    server, _, port = await _clamd(b"INSTREAM size limit exceeded. ERROR")
    async with server:
        with pytest.raises(ScannerUnavailable):
            await ClamdScanner("127.0.0.1", port).scan(io.BytesIO(b"x"))
    with pytest.raises(ScannerUnavailable):  # nothing listening
        await ClamdScanner("127.0.0.1", 1, timeout=1).scan(io.BytesIO(b"x"))


# --- extraction ----------------------------------------------------------------------------------
def test_extract_pdf_text_layer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "a.pdf"
    p.write_bytes(fx.pdf_text("Clause one of the agreement says something long enough.", "Second page has text as well, plenty."))
    ocr = fx.FakeOcr()
    ex = extract("pdf", p, ocr)
    assert [pg.n for pg in ex.pages] == [1, 2] and all(pg.method == "text" for pg in ex.pages) and ocr.calls == 0


def test_extract_scanned_pdf_uses_ocr_and_flags_low_confidence(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "scan.pdf"
    p.write_bytes(fx.pdf_scanned(2))
    good = extract("pdf", p, fx.FakeOcr("recognised words here", 0.93))
    assert [pg.method for pg in good.pages] == ["ocr", "ocr"] and not any(pg.needs_review for pg in good.pages)
    bad = extract("pdf", p, fx.FakeOcr("garbled", 0.40))
    assert all(pg.needs_review for pg in bad.pages)
    none = extract("pdf", p, fx.FakeOcr(available=False))
    assert all(pg.method == "none" and pg.needs_review and pg.text == "" for pg in none.pages) and "ocr_unavailable" in none.notes


def test_extract_password_protected_pdf(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "locked.pdf"
    p.write_bytes(fx.pdf_encrypted())
    ex = extract("pdf", p, fx.FakeOcr())
    assert ex.password_protected and ex.pages == []
    assert pymupdf is not None


def test_extract_docx_text_and_image(tmp_path) -> None:  # type: ignore[no-untyped-def]
    d = tmp_path / "a.docx"
    d.write_bytes(fx.docx_bytes("1. Termination", "Either party may terminate."))
    assert "Either party may terminate." in extract("docx", d, fx.FakeOcr()).pages[0].text
    i = tmp_path / "a.png"
    i.write_bytes(fx.png_bytes())
    pg = extract("image", i, fx.FakeOcr("image text", 0.9)).pages[0]
    assert pg.text == "image text" and pg.method == "ocr"
    t = tmp_path / "a.txt"
    t.write_bytes("latin é".encode("latin-1"))
    assert extract("text", t, fx.FakeOcr()).pages[0].text.startswith("latin")


# --- classification -------------------------------------------------------------------------------
def test_classification() -> None:
    ins = classify("Insurance policy schedule. Sum insured KES 2,000,000. Premium payable annually. Exclusions apply.")
    assert (ins.doc_type, ins.practice_area) == ("insurance_policy", "insurance") and ins.confidence > 0.5
    assert classify("EMPLOYMENT AGREEMENT: the employee and employer agree; probation 3 months; annual leave 21 days").doc_type == "employment_contract"
    assert classify("This agreement between the parties. Governing law: Kenya. Either party may terminate.").doc_type == "contract"
    assert classify("lorem ipsum dolor sit amet").doc_type is None
    assert classify("Insurance policy. The patient has a medical diagnosis. Sum insured KES 5. Premium.").sensitivity == "sensitive"
    assert classify("Sum insured and premium under this insurance policy").sensitivity == "normal"


# --- classification: court decisions, orders, claim forms, medical reports, leases ---------------
JUDGMENT = """REPUBLIC OF KENYA
IN THE HIGH COURT OF KENYA AT MOMBASA
CIVIL APPEAL NO. 40 OF 2024
BETWEEN
JOHN DOE ........ APPELLANT
AND
ACME LTD ........ RESPONDENT
JUDGMENT
Coram: Justice X
1. This is an appeal from the judgment of the trial court.
2. The issues for determination are whether the appellant was negligent.
3. The appeal is dismissed with costs of the appeal to the respondent.
Judgment delivered at Mombasa this 3rd day of May 2026."""

PLAINT = """IN THE HIGH COURT OF KENYA AT NAIROBI
CIVIL SUIT NO. 12 OF 2026
PLAINT
1. The Plaintiff is a limited liability company.
2. The Defendant is an individual residing in Nairobi.
3. On 1st March 2026 the Defendant breached the contract.
REASONS WHEREFORE the Plaintiff prays for judgment against the Defendant for KES 2,000,000 and costs of the suit."""


def test_classify_judgment_but_not_a_plaint() -> None:
    assert classify(JUDGMENT).doc_type == "judgment"
    assert classify(PLAINT).doc_type == "court_pleading"


def test_classify_court_order_claim_form_medical_report_and_lease() -> None:
    order = "IN THE HIGH COURT OF KENYA\nORDER\nUpon hearing counsel IT IS HEREBY ORDERED THAT the respondent pays.\nGIVEN under my hand and the seal of the court."
    assert classify(order).doc_type == "court_order"
    form = "MOTOR CLAIM FORM\nName of insured: A\nDate of loss: 1/2/2026\nPlace of loss: Thika road\nI declare that the above is true."
    assert classify(form).doc_type == "claim_form"
    med = "MEDICAL REPORT\nThe patient was admitted on 2 March and discharged on 5 March.\nDiagnosis: fracture of the left femur."
    assert classify(med).doc_type == "medical_report"
    assert classify(med).sensitivity == "sensitive"
    lease = "THIS LEASE is made between A (the Lessor) and B (the Lessee). The rent is KES 50,000. This agreement is governed by the laws of Kenya and either party may terminate it."
    assert classify(lease).doc_type == "tenancy_agreement"


def test_policy_that_mentions_a_claim_form_stays_a_policy() -> None:
    text = ("Insurance policy schedule. Sum insured KES 2,000,000. Premium payable annually. Exclusions apply. "
            "To claim, the insured must complete a claim form within 30 days.")
    assert classify(text).doc_type == "insurance_policy"
