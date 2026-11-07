# ADR 0004 — Practice areas are installable packs

- Status: Accepted
- Date: 2026-11-07

## Context
Lygo is a toolkit for legal firms in general (fact-checking, case building, document and legal
comparison, insurance, advisory), not a single-practice-area product. Hard-coding one area (the
original insurance example) would force code changes for every new area.

## Decision
A **pack** is a directory `app/engine/packs/<id>/` containing:

| File | Purpose | Status |
|---|---|---|
| `pack.yaml` | id, title, practice area, version, doc types, `reviewed_by` | implemented |
| `rules.yaml` | declarative rules (see doc 04) | implemented |
| `extraction.yaml` | typed field schemas per doc type | planned |
| `comparison.yaml` | comparison profiles (which fields/clauses matter) | planned |
| `playbook.yaml` | starter firm positions | planned |

Firms enable packs (`pack_installations`, planned) and tune thresholds. Initial packs:
`core_contract` (applies to any firm) and `insurance`. More are added as content, not code.

**Review gate:** a pack with an empty `reviewed_by` is a draft. It loads in `local`/`staging` but
`load_rule_pack` refuses it when `APP_ENV=production`. Advocate sign-off is a content step.

## Consequences
- Adding a practice area is a review-and-fixtures exercise, not an engineering project.
- Advocate review capacity, not code, becomes the pacing item.
- Firms cannot run arbitrary Python: firm-authored rules are declarative only.
