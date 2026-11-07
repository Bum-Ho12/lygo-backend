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
| B | Members and invitations, clients (+ portal invitations), matters, deadlines, tasks, platform firm bootstrap | done (migration 0003, 20 API + 3 DB-level tests). Todo: consents, `matter_facts`, pack installation, suspend/revoke portal users |
| C | Storage, both ingest doors, ingestion pipeline | mostly done (see below): storage adapters (local, S3), BYO S3 destinations with envelope-encrypted credentials and residency rules, both upload endpoints, sniffing, malware scan, dedup, versions, OCR interface, parse, structure, classify, typed fields + entities, rules, flags, review queue, SSE progress, comparison endpoint with field diffs. Todo: bulk ZIP, presigned uploads, email-in, Azure/GCS/SFTP/webhook adapters, per-artefact storage policy (only "primary destination" exists), scheduled exports, field-level encryption for ID numbers |
| D | Packs engine: rules + registry, `core_contract` and `insurance` draft packs | partial: rules engine, registry and 2 draft packs done; extraction/playbooks todo |
| D2 | Deterministic comparison engine | partial: aligner, field diff and hints done; persistence, API, jobs todo |
| E1 | RAG: chunking, embeddings, hybrid retrieval, corpus loader and versioning | done except real-model validation: `chunks` (pgvector HNSW + GIN full text, RLS), statute/clause chunkers, hashing + OpenAI-compatible embedders, RRF hybrid search with as-at filtering, citation objects, corpus loader (licence gate, version supersession) + platform endpoint + CLI, indexing stage in the pipeline, search endpoints. Todo: reranker, real embedding model + quality evaluation, judgment chunker, chunk-table partitioning, re-embedding migration tooling |
| E2 | AI providers, prompts, orchestrator, guardrails, memory, chat | done against a fake model (see "Phase E2 status"); NOT validated with a real LLM. Todo: AI materiality labels for comparisons (E3), drafting/case-builder tasks, model-based advice classifier, entailment checking, golden-set evaluation on a real model |
| E3 | Fact-check, advisory memo, chronology (conflict check deferred) | todo |
| F | Notifications, SMS, email, webhooks | partial: Thrivewave adapter (docs obtained, key verified without sending), template registry with privacy policy, quiet hours, quota, safe retry/claim logic, unsigned-DLR token scheme, notification audit list. Todo: OTP login flow, wiring triggers (document reviewed, deadlines), Celery beat for `send_due`, real email adapter, outbound webhooks, DPA with Thrivewave |
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

## Phase C status (ingestion)
Implemented: `POST /knowledge/documents`, `POST /clients/{id}/documents`, `GET` lists, `GET /documents/{id}` (status timeline),
`/content`, `/flags`, `PATCH /flags/{id}`, `POST /documents/{id}:reprocess`, `GET /review-queue`, `POST /comparisons`.
Pipeline stages recorded in `ingest_events`: `start -> parse -> structure -> classify -> rules`. Rules run on client documents only.

**Verified vs not verified**
- Verified in tests: S3 adapter against moto (not real MinIO), clamd protocol against a fake server (not real ClamAV),
  OCR through a fake engine (no Tesseract on the dev machine), PDF/DOCX/text extraction, password-protected PDFs.
- Not yet exercised: real Tesseract accuracy (Swahili models, handwriting), real ClamAV, MinIO/RumptyCloud, Celery worker
  with Redis (`INGEST_MODE=celery` path is written but untested), large-file performance (20-page / 2-minute target).

**Security notes**
- Uploads are type-sniffed from content (extension must agree), size-capped, scanned, and never stored when rejected.
  Outside `local`, ingestion fails closed (503) unless `CLAMD_HOST` is configured. The dev scanner only knows EICAR.
- Knowledge-base documents shared with clients are readable but not writable by clients (separate per-command RLS policies);
  derived rows (pages, sections, flags) are never shared.
- Audit inserts omit `RETURNING` so client users, who cannot read audit rows, can still generate audited events.

## Phase E1 status (retrieval)
**Verified:** isolation (13 DB-level and API-level checks incl. the "forgotten filter" case), as-at law versions, idempotent corpus
reload, indexing replaced on reprocess, embedding outage leaves the parsed document intact, hybrid ranking reports both signals.

**Not verified / caveats**
- Retrieval *quality* is untested: the default embedder is a lexical hashing embedder (`hashing-v1`), not a semantic model. Run
  the golden-set evaluation (`tests/eval`) before trusting ranking; the OpenAI-compatible client is tested against a mock only.
- `chunks.embedding` is `vector(1024)`. A model with another dimension needs a migration and a full re-index (the indexer
  rejects wrong-sized vectors rather than writing them).
- The synthetic Act used in tests is fake. **No real legislation is loaded**; real sources require the NCLR licence (OPEN-QUESTIONS #5);
  production loading is blocked unless the manifest sets `licence_confirmed: true`.
- HNSW with RLS filtering relies on pgvector >= 0.8 iterative scans (local test DB is 0.8.x); check recall on the production
  Postgres version before launch.

## Phase C gap-closing status
- **BYO storage:** `POST/GET /firms/{id}/storage-destinations`, `/test`, `PUT /storage-policy/primary`, `DELETE`. Owner-only. Credentials are
  sealed with AES-256-GCM under a per-firm data key (wrapped by `MASTER_KEY`; a local dev key is derived only when `APP_ENV=local`),
  bound to the firm via AAD, never returned or logged. A destination must pass a write/read/delete probe before it can be primary.
  Outside Kenya a `transfer_basis` is mandatory (cross-border rules, docs/07). New uploads follow the primary; existing documents
  stay where they are. Wiping `firms.wrapped_dek` (crypto-shredding) makes BYO data unreachable and uploads fail closed (503).
  *Not covered:* real MinIO/RumptyCloud/AWS buckets (tested with moto), KMS/Vault key provider (only the env-key provider exists),
  key rotation, per-artefact policies.
- **Typed fields and entities:** amounts, periods, percentages, dates, parties and reference numbers with context labels
  (`GET /documents/{id}/fields`), firm-wide entities (`GET /entities`, firm-only) as the seed for conflict checks, and field diffs in
  `POST /comparisons`. National ID numbers are deliberately not extracted. Extraction is regex-based: expect misses and false
  positives on real documents; a model fallback is planned with the AI engine.
- **SSE progress:** `GET /documents/{id}/events` (resumable with `?after=` / `Last-Event-ID`, ends with `event: done`).
  Implemented by polling the database (default 0.5 s), not by push; adequate for tens of concurrent viewers.

## Phase E2 status (AI engine)
**Built:** provider interface (OpenAI-compatible + deterministic fakes), versioned prompts (`explain-v1`, `research-v1`, `summarise-v1`),
orchestrator (retrieve -> prompt -> LLM -> verify -> one retry -> abstain), guardrails, chat API (JSON and SSE), approval workflow,
per-firm AI settings, matter facts, rolling conversation summary, document summaries, monthly message cap, golden-set harness (`make eval`).

**Guardrails and how far they go (read before trusting them)**
| Guardrail | What it enforces | What it does NOT do |
|---|---|---|
| Citation verification | every `[S#]` marker maps to a supplied source; sections/case names/report citations must appear in the sources; uncited substantive sentences are dropped; no valid citation left => abstain | It does **not** check that a cited sentence is *entailed* by the source. A model could cite S1 and misstate what S1 says. Entailment checking (NLI/LLM judge) is not built. |
| Grounding | abstain unless a passage covers >= 40% of the question's terms or similarity >= `RAG_MIN_SIMILARITY` | Thresholds are tuned for the hashing embedder; they must be re-tuned for the real model. |
| Advice boundary | regex detection of decision/prediction/drafting requests (English) -> general explanation + escalation task; advisory phrasing stripped from answers; disclaimer always appended | Rule-based: paraphrases and Swahili will slip through. A model-based classifier is needed. |
| Approval mode | default ON; pending/rejected replies are invisible to clients **in the database** (RLS); clients cannot insert, edit or delete AI replies | An approved answer is only as good as the advocate's review. |
| Prompt injection | instruction-like spans in sources/questions/firm notes are replaced; delimiters stripped | Pattern based: novel phrasings are not caught. Mitigated by the rule that only cited, verified statements survive and by no tool use. |
| Memory | last 8 turns + rolling summary; only advocate-confirmed facts are given to the model | AI-proposed facts are not built yet. |

**Design decisions to revisit**
- SSE carries progress events and then the finished verified message, **not tokens**: answers are verified before release, so the "first token < 3 s p95" target is not met by design.
- `citations` are stored on the message (JSON) rather than in the separate table sketched in doc 09.
- The question is stored before the model runs; if the model is down the user keeps a dangling question and can retry.
- Document summaries are not counted against the monthly cap and are not stored as chat messages (audited only).
- Client chat defaults: chatbot OFF, approval ON (OPEN-QUESTIONS #9 answered conservatively until pilot firms say otherwise).

**Found by testing and fixed during this phase:** full-text search used AND semantics so natural-language questions never matched
(E1 bug); citation markers placed after the full stop were attached to the next sentence; the case-name check rejected legitimate
cases; the advice filter wrongly stripped policy obligations ("you must pay by..."); clients could delete/alter released AI replies
at the SQL level (now blocked by RLS).
