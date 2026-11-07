# 14 — Implementation plan and status

Lygo is a toolkit for legal firms in general: fact-checking, case building, document and legal
comparison, insurance, advisory and general speed-up of firm work. Practice areas are installable
[packs](adr/0004-practice-area-packs.md). Status key: **done**, **partial**, **todo**, **blocked**.

## Defaults assumed (change by editing this file and the ADRs)
- Client portal stays *explain-only*; comparison, fact-check, advisory and conflict check are firm-only.
- Fact-checking runs only against the corpus and the firm's own documents (no web verification in MVP).
- First packs: `core_contract`, `insurance`. Conflict check is deferred to a later release.

## Phases

| Phase | Scope | Status |
|---|---|---|
| A1 | Lint/type baseline, CI workflow, Makefile | done |
| A2 | Config validation, structured logging with PII masking, problem+json, cursor pagination, request IDs, liveness/readiness | done |
| A3 | DB layer: engines, base models, Alembic, `enable_tenant_rls` helper | todo (needs Docker/Postgres to verify) |
| A4 | Tenancy dependency; fix `tenancy.py` and RLS sketches (see "Known design fixes") | todo (needs Postgres) |
| A5 | Auth, MFA, RBAC, API keys ([ADR 0006](adr/0006-auth-bootstrap.md)) | todo |
| A6 | Append-only audit log | todo |
| B | Firms, members, clients, matters, deadlines, consents, tasks, pack installation | todo |
| C | Storage adapters; both ingest doors; OCR/parse pipeline; structure tree, typed fields, entities, versions | partial: structure parser done (`app/ingestion/structure.py`) |
| D | Packs engine: rules + registry, `core_contract` and `insurance` draft packs | partial: rules engine, registry and 2 draft packs done; extraction/playbooks todo |
| D2 | Deterministic comparison engine | partial: aligner, field diff and hints done; persistence, API, jobs todo |
| E1 | RAG: chunking, embeddings, hybrid retrieval, corpus loader and versioning | todo |
| E2 | AI providers, prompts, orchestrator, guardrails, memory, chat, AI materiality layer | todo |
| E3 | Fact-check, advisory memo, chronology (conflict check deferred) | todo |
| F | Notifications, SMS (Thrivewave **blocked** on API docs), email, webhooks | todo |
| G | Reports, analytics, exports, offboarding | todo |
| H | Observability, security testing, load tests, backups, launch checklist | todo |

## Known design fixes (apply when implementing A3/A4)
- `tenancy.py` calls `session.begin()`; this fails if the session already autobegan. Design the session factory and request dependency around one explicit transaction.
- RLS policies must use `current_setting('app.firm_id', true)` with `NULLIF`, and allow `scope = 'public'` rows (where `firm_id IS NULL`).
- `vector(N)` is fixed per migration: choose the embedding dimension before the chunks migration.
- Add a platform role/context and a break-glass path to RLS; `platform_admin` must not see content by default.
- Celery: align queue names (`ingest`, `ai`, `notify`, `sms`) across `task_routes`, compose and Dockerfile; run OCR in its own worker image.
- Comparison outputs spanning two clients are firm-role-only (ADR 0005).

## Data model additions (to add to doc 09 with their migrations)
`practice_packs`, `pack_installations`, `document_sections`, `document_fields`, `document_versions`,
`entities`, `entity_mentions`, `comparisons`, `comparison_items`, `fact_checks`, `claims`,
`claim_evidence`, `playbooks`, `playbook_positions`, `tasks`, `chronology_events`, and a
`jurisdiction` column on `corpus_documents`.

## Blocked on decisions or third parties
LLM and embedding model (OPEN-QUESTIONS #6), Thrivewave API (#2), NCLR licence (#5), RumptyCloud terms (#3).
All are built behind interfaces with fakes so development continues.
