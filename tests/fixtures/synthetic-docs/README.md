# Synthetic test documents

Everything here is **synthetic**: parties, advocates, judges, insurers, case numbers and amounts are fictional.
Every page carries a "SYNTHETIC TEST DOCUMENT" footer. Statute sections marked `statute_in_corpus` are real provisions checked
against the official Acts PDFs (Constitution, Insurance, Insurance Professionals, Retirement Benefits, NSSF, Affordable Housing) on 2026-11-07. Safe for staging and CI (docs/11: staging is synthetic-only); never mix into real firm workspaces.

| Folder | Upload through | Contents |
|---|---|---|
| `Judgements-synthetic/` | `POST /v1/firms/{firm}/knowledge/documents` (scope `firm_kb`) | 11 judgments/rulings + 1 corrected version + 1 byte-identical duplicate |
| `Client-documents-OCR/` | `POST /v1/firms/{firm}/clients/{client}/documents` (scope `client`) | 16 client documents as clients really deliver them (scans, phone photos, fax, handwriting) + 1 duplicate + 1 amended version |
| `ground-truth/manifest.json` | do not upload | Expected type, sensitivity, flags, citations (with class), amounts, versions, duplicates per file |
| `ground-truth/text/*.txt` | do not upload | Exact source text of every file, for OCR accuracy (CER/WER) scoring |
| `scripts/synthetic_docs/` (repo root) | – | Scripts that rebuilt this folder (fixed seed: same content each run; PDF/DOCX bytes differ because of embedded timestamps); `validate.py` runs the set through the app's own code |

## Judgments (firm knowledge base)

| ID | What it tests |
|---|---|
| J01 (+ dup) | Insurance (Takaful), cites Insurance Act s.19A and Constitution Art.40; duplicate must be caught by SHA-256 dedup |
| J02 | Word file with typed paragraph numbers; NSSF Act s.19, s.22 (in corpus) and Employment Act s.45 (not in corpus) |
| J03 | Word **auto-numbered** paragraphs (numbers vanish on extraction); minors + medical data -> `sensitive` |
| J04 / J04b | Original and slip-rule corrected judgment: versioning + comparison (60 -> 90 days, remittal added) |
| J05 | Auto-numbered Word; Affordable Housing Act s.4, s.7 |
| J06 | **Password-protected** PDF (`lygo-test`) |
| J07 | Children's case with initials, learning disability -> `sensitive` |
| J08 | Tenancy ruling; cited by J09 |
| J09 | Cites J01 and J08 -> citations that must resolve **inside the firm KB** |
| J10 | Contains a **fabricated** Insurance Act s.312 and a non-existent case -> citation guardrail must reject |
| J11 | 2015 photocopied ruling, **image-only PDF** -> OCR path |

## Client documents (OCR)

| ID | Delivered as | What it tests |
|---|---|---|
| C01 | 2-page clean scan | Insurance pack: pre-existing exclusion, 14-day notice window; health wording -> `sensitive` |
| C02 | Office scan | 7-day notice window; **policy schedule missing** flag |
| C03 | Phone photo, handwritten form | Perspective, shading, handwriting -> review queue |
| C04 | Scan with stamp + signature | Letter classification; stamp/signature noise over text |
| C05 | Binarised fax TIFF | Low-resolution tables |
| C06 | 3-page scan, **page 2 sideways** | Orientation detection; auto-renewal, 90-day notice, indemnity flags |
| C07 / C18 | Stained scan / amended version | Contract flags; comparison: probation 3 -> 6 months, salary 32,000 -> 38,500 |
| C08 | Phone photo payslip | Table extraction (NSSF, SHIF, Housing Levy deductions) |
| C09 | PNG statement | Contribution gaps (links to J02 facts) |
| C10 / C17 | Clean scan / identical re-upload | Dedup on client uploads |
| C11 | Phone photo, **Swahili** | Swahili OCR model (`swa`), language detection |
| C12 | **Mixed PDF** | Page 1 text layer, page 2 scanned with "certified true copy" stamp |
| C13 | 2 handwritten pages, poor copy | Every page should land in the review queue |
| C14 | Scan, medical report | Must be tagged `sensitive` |
| C15 | Near-blank noisy page | Empty OCR output must not crash and should be flagged |
| C16 | Password-protected digital PDF | Pipeline must stop and ask for the password, not OCR |

## What `validate.py` found against the current code (2026-11-07)

Run from the repo root: `PYTHONDONTWRITEBYTECODE=1 uv run python scripts/synthetic_docs/validate.py tests/fixtures/synthetic-docs`
(scanned pages are scored with the ground-truth text as a perfect-OCR stand-in, because Tesseract is not installed).

Behaves as expected: file sniffing, password detection (J06, C16), image-only pages routed to OCR (all scans), mixed PDF
(C12: page 1 `text`, page 2 OCR), dedup (J01-dup, C17), and flags on C01, C02, C04, C07, C10, C18.

Gaps these files exposed, and their status:
1. **Fixed:** new `judgment`, `court_order`, `claim_form` and `medical_report` types in `taxonomy.yaml`; leases ("lease agreement",
   "this lease", "tenancies") now classify as `tenancy_agreement`. All 12 judgments, C03, C06, C12 and C14 classify as expected.
2. **Fixed (defence in depth):** `_applies()` in `app/engine/rules/evaluator.py` no longer runs type-scoped rules on unclassified
   documents. The ingestion pipeline already skipped such packs (`_run_rules`), so live uploads were not affected; direct callers
   of `evaluate_pack` were.
3. **Fixed:** Word automatic numbering is rebuilt during DOCX extraction (`_DocxNumbering` in `app/ingestion/parsers/extract.py`):
   style-inherited and direct numbering, multi-level formats, start overrides, `numId 0`. J03, J05, J09 now keep 9/9, 8/8, 7/7.
4. Open: "ends this contract" wording does not count as a termination clause (C07/C18) - decide whether that is intended.

Caveat: the new taxonomy patterns were tuned on this synthetic set plus independent unit-test texts; re-check them on real
judgments before relying on the accuracy.

## Other documents still needed for OCR testing (not generated here)

Deliberately **not** synthesised, because realistic fakes would be forged identity or official documents. Use redacted real samples
under a DPA, or blank official templates filled with obvious test values:
- National ID / passport bio page, KRA PIN certificate, Certificate of Incorporation / CR12
- Title deeds, official land searches, green cards, allotment letters
- Police abstracts, OB extracts, P3 forms
- Court-stamped pleadings with registry receipts, gazette notices
- Work permits and immigration passes (for an immigration pack later)

Also worth adding later: multi-language pages (English + Swahili), carbon-copy receipts, very large scans (100+ pages for the
20-page / 2-minute target), photos with fingers or glare, and documents with handwritten annotations on printed text.

## Regenerating

The generator needs `reportlab pillow numpy pymupdf python-docx` (not project dependencies; use a throwaway venv):

```bash
python -m venv /tmp/synth && /tmp/synth/bin/pip install reportlab pillow numpy pymupdf python-docx
/tmp/synth/bin/python scripts/synthetic_docs/gen.py tests/fixtures/synthetic-docs   # rewrites the three data folders
```
