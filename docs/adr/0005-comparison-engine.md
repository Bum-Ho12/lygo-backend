# ADR 0005 — Deterministic comparison engine with an AI explanation layer

- Status: Accepted
- Date: 2026-11-07

## Context
Firms need to compare document versions, two documents (e.g. two insurance policies), a document
against a template or playbook, and statute versions. This follows ADR 0003: be deterministic
where possible, use the AI only to explain and label.

## Decision
`app/engine/compare` aligns two `DocumentText` trees clause by clause and produces a
`ComparisonResult`:
1. **Alignment** in three passes: same number + heading; same heading (renumbered/moved);
   best text similarity above a threshold (greedy, ordered by score).
2. **Classification**: `unchanged | modified | added | removed | moved`.
3. **Detail**: word-level diff, typed field changes (money, durations, percentages, dates) and
   deterministic hints (`numeric_or_date_change`, `negation_changed`, `obligation_changed`, `renumbered`).
4. **AI layer (planned, Phase E)**: rates materiality and writes plain-language explanations per
   change. Anything the AI adds is labelled "AI-suggested". It never alters the alignment.

Same input -> same output, so results can be cached by (document hashes, mode, prompt version).

## Isolation
A comparison can span two clients' documents. Its output is **firm-role-only**: stored without a
`client_id`, with a firm-only RLS policy, and never indexed into client-retrievable scopes. A
client user may only compare their own documents.

## Consequences
- Comparison works with no LLM and no corpus, so it can ship early.
- Quality depends on the structure parser (`app/ingestion/structure.py`); scanned/OCR documents
  with poor numbering fall back to similarity matching.
- Needs `document_sections`, `document_fields` and document version lineage in the data model.
