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
| A3 | DB layer: engines, base models, Alembic, `tenant_rls_sql` helper, migration 0001 (identity, firms, clients, matters, audit) | done (verified on Postgres 17 + pgvector) |
| A4 | Tenant session (`tenant_session`), fixed RLS policies, isolation test suite (16 tests) | done; the request dependency that derives the context from a JWT lands with A5 |
| A5 | Auth ([ADR 0006](adr/0006-auth-bootstrap.md)) | partial: register/login/refresh (rotation + reuse detection)/logout, `/me`, role-permission matrix, tenant context from token + path firm, `GET/PATCH /firms/{id}`, `GET /firms/{id}/clients`. Todo: TOTP MFA, SMS OTP, invitations, SSO, API keys, login rate limiting, restrict `users` column access |
| A6 | Append-only audit log (privileges + trigger; `domain/audit.record`) | done |
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

## Known design fixes (A3/A4 items applied; remaining items open)
- ~~`tenancy.py` session handling~~ fixed: `tenant_session(factory, ctx)` owns one explicit transaction.
- ~~RLS policies~~ fixed: helper functions use `NULLIF(current_setting(.., true), '')`; `public_scope` allows `scope='public'` rows for firm *and* client users (the isolation tests caught client users being blocked from public law).
- `vector(N)` is fixed per migration: choose the embedding dimension before the chunks migration.
- Platform context done (platform roles manage `firms` and see no firm content). Break-glass support sessions still todo.
- `users` is readable by the app role in full (ADR 0006). Before auth ships, restrict via column grants/views so one tenant cannot enumerate other users.
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
