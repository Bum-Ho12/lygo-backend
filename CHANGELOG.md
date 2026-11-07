# Changelog

## [0.1.0] — 2026-11-07
- Project structure, design documentation, ADRs, local dev environment and app skeleton.

## Unreleased
- Foundations: config validation, PII-masking structured logging, problem+json errors, cursor pagination, request IDs, readiness endpoint.
- Practice-area pack system (ADR 0004) with draft `core_contract` and `insurance` rule packs and a review gate for production.
- Deterministic rules evaluator, clause-tree parser, and document comparison engine (ADR 0005).
- Database layer: Alembic, migration 0001 (identity, firms, clients, matters, append-only audit log), forced RLS with helper functions, `tenant_session`, and a 16-test tenant isolation suite run as the non-owner role.
- Auth: Argon2id passwords, JWT access tokens, rotating refresh tokens with reuse detection, RBAC permission matrix, tenant context resolved from token + path firm (non-members get 404), first firm/client endpoints, 11 end-to-end API tests.
- Phase B: platform firm bootstrap, invitation flow (token-hash RLS policy, email-bound, single use), member management with last-owner protection, clients and portal users, matters with assignee validation, deadlines, firm-only tasks.
- Phase C: storage adapters (local, S3), both ingest endpoints, content sniffing, malware scan (clamd client), dedup and versions, pipeline (parse, OCR interface, structure, classify, rules), review queue, flag workflow, on-demand document comparison, shared-KB RLS policies.
- Notifications: Thrivewave SMS adapter built from the provider docs, template/privacy policy, quiet hours, per-firm quota, claim-before-send with no blind retries, token-authenticated delivery reports, erase-on-terminal PII handling.
- Fix: `cp .env.example .env` no longer crashes configuration loading (comma-separated `FRONTEND_ORIGINS`).
- Retrieval (E1): `chunks` with pgvector + full text and RLS, statute and clause chunkers, embedder interface (hashing + OpenAI-compatible), hybrid RRF search with as-at law versions and citations, corpus loader with licence gate, pipeline indexing stage, search endpoints.
- Storage (BYO): envelope-encrypted S3-compatible destinations with probes, residency/transfer-basis rules, primary destination routing, crypto-shredding.
- Ingestion: typed fields and firm entities (no national IDs), document-level field diffs in comparisons, SSE progress endpoint.
- AI engine (E2): LLM provider interface (OpenAI-compatible + deterministic fakes), versioned prompts, orchestrator with verify/retry/abstain, guardrails (citation verification, grounding, advice boundary, prompt-injection neutralisation), per-firm approval mode enforced by RLS, chat API (JSON + SSE), matter facts and rolling memory, document summaries, golden-set evaluation harness.
- Fix: full-text search now matches natural-language questions (OR semantics); grounding judged by keyword coverage.
- Embeddings: Voyage AI (`voyage-law-2`) with document/query input types, batching and backoff; embedder interface gains `embed_query`.
- Email: SMTP adapter, privacy-enforcing templates, invitation delivery status + resend, password reset and security-alert emails.
- Auth hardening (ADR 0007): durable rate limiting/lockout, SMS OTP (phone verification + second factor), TOTP with replay protection and recovery codes, MFA enforcement for staff, password change/reset that cannot bypass MFA, session revocation.
- Claude (Anthropic SDK) as the chat LLM: no sampling params, refusal handling, server-side fallbacks, per-task effort; verifier rebuilt from live output (headings/questions/coverage notes exempt, formatting preserved).
- E3: fact-check (deterministic cite-check + verbatim-evidence claim-check), matter chronology, case builder.
- Email-less mode: `email_verified_at` removed; invitation links returned to the inviter; audited platform-issued reset links.
- Tests are hermetic (no real credentials); `EVAL_REAL_MODEL=1` runs the golden set on the real model.
- Python/tooling managed by uv (`.python-version`, `uv.lock`).
- CI workflow (lint, types, security scan, unit tests, tenancy job), Makefile targets.
