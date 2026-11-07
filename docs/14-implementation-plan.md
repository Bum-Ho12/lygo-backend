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
| A5 | Auth ([ADR 0006](adr/0006-auth-bootstrap.md), [ADR 0007](adr/0007-authentication-factors-and-email.md)) | mostly done: password login with durable rate limiting/lockout, TOTP + recovery codes, SMS OTP (phone verification + second factor), MFA enforcement for staff, password reset by email, password change, session revocation, email verification marks. Todo: SSO (OIDC), WebAuthn/passkeys, API keys, restrict `users` column access, device trust, SIM-swap signals |
| A6 | Append-only audit log (privileges + trigger; `domain/audit.record`) | done |
| B | Members and invitations, clients (+ portal invitations), matters, deadlines, tasks, platform firm bootstrap | done (migration 0003, 20 API + 3 DB-level tests). Todo: consents, `matter_facts`, pack installation, suspend/revoke portal users |
| C | Storage, both ingest doors, ingestion pipeline | mostly done (see below): storage adapters (local, S3), BYO S3 destinations with envelope-encrypted credentials and residency rules, both upload endpoints, sniffing, malware scan, dedup, versions, OCR interface, parse, structure, classify, typed fields + entities, rules, flags, review queue, SSE progress, comparison endpoint with field diffs. Todo: bulk ZIP, presigned uploads, email-in, Azure/GCS/SFTP/webhook adapters, per-artefact storage policy (only "primary destination" exists), scheduled exports, field-level encryption for ID numbers |
| D | Packs engine: rules + registry, `core_contract` and `insurance` draft packs | partial: rules engine, registry and 2 draft packs done; extraction/playbooks todo |
| D2 | Deterministic comparison engine | partial: aligner, field diff and hints done; persistence, API, jobs todo |
| E1 | RAG: chunking, embeddings, hybrid retrieval, corpus loader and versioning | done except real-model validation: `chunks` (pgvector HNSW + GIN full text, RLS), statute/clause chunkers, hashing + OpenAI-compatible embedders, RRF hybrid search with as-at filtering, citation objects, corpus loader (licence gate, version supersession) + platform endpoint + CLI, indexing stage in the pipeline, search endpoints. Todo: reranker, real embedding model + quality evaluation, judgment chunker, chunk-table partitioning, re-embedding migration tooling |
| E2 | AI providers, prompts, orchestrator, guardrails, memory, chat | done against a fake model (see "Phase E2 status"); NOT validated with a real LLM. Todo: AI materiality labels for comparisons (E3), drafting/case-builder tasks, model-based advice classifier, entailment checking, golden-set evaluation on a real model |
| E3 | Fact-check, advisory memo, chronology (conflict check deferred) | mostly done: cite-check + claim-check, chronology, case builder (see "Phase E3 + Claude status"). Not built: drafting (Advocates Act s.34), conflict-check endpoint (entities exist), AI-proposed matter facts, persisted comparisons, AI materiality labels |
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

## Auth hardening + Voyage + email status
- **Voyage:** `VoyageEmbedder` (default `voyage-law-2`: legal retrieval, exactly 1024 dims, 16K-token context) with `document`/`query` input types,
  count+token batching, 429/5xx backoff. *Not run against the real API (no key yet).* After setting `VOYAGE_API_KEY`, re-index existing
  chunks if any were created with another embedder (`embed_model` is stored per chunk) and re-tune `RAG_MIN_SIMILARITY`.
  **Compliance:** document text is sent to Voyage (US): add it to the sub-processor list, sign a DPA with no-retention/no-training terms
  (verify Voyage's terms; not assumed here), cover the cross-border transfer, and honour the sensitive-data consent rule (docs/07).
- **Email:** SMTP adapter tested against a real SMTP server (aiosmtpd); template registry with the privacy policy; invitation delivery
  status + resend. *Not tested against a real provider; SPF/DKIM/DMARC and a sending domain are still needed.*
- **MFA/OTP:** see ADR 0007. Rate limiting is Postgres-backed (no Redis needed). Mutation-checked: removing TOTP replay protection,
  MFA enforcement or the lock-before-password check each breaks tests.
- **Known gaps:** `MFA_REQUIRED_FOR_FIRM_ROLES` defaults to false locally (required true in production); a user with no second factor
  enrolled cannot reach firm data when enforcement is on until they enrol (they can still reach `/me/*`); account lockout is per
  account+IP to avoid trivial victim lockout, so slow distributed guessing is bounded only by the account-wide limit; access tokens
  stay valid up to 15 minutes after a password change/sign-out (refresh tokens are revoked immediately).

## Phase E3 + Claude status
**Claude (Anthropic SDK 2.6, `claude-opus-5-5`):** the chat LLM. Verified live: key accepted; golden set passes on the real model; adversarial probes
(invented section, out-of-scope question, injected "admin mode" instruction in a source, advice-seeking client) all behaved safely.
Adapter rules: no sampling params (removed on Claude 5.x), thinking left on with a >= 4096-token ceiling (thinking tokens count against `max_tokens`),
`stop_reason: "refusal"` -> abstain, server-side `fallbacks="default"` (beta `server-side-fallback-2026-07-01`) for models that support it.
Per-task effort: explain/summarise `low`, research `medium`, case builder `high`. Measured latency on Opus 5.5: client explanation ~4.5 s,
research ~11 s. **Cost is not tuned**: Opus 5.5 is $4/$20 per MTok; `ANTHROPIC_MODEL=claude-sonnet-5-5` is half the price. Decide after running the
golden set (`EVAL_REAL_MODEL=1 make eval`) on both.
**Compliance:** matter excerpts and questions are sent to Anthropic (US). Add Anthropic to the sub-processor list, sign the DPA, confirm retention /
no-training terms for the account type in use, and cover the cross-border transfer (docs/07). Do not assume terms for the key's account type.

**What changed because of live testing:** the citation verifier removed 15 legitimate lines of a correct research note and flattened its markdown.
It now (a) requires a marker per sentence via the prompt, (b) exempts headings, questions, "the sources do not cover..." and "check/confirm..." lines
(they still get the invalid-marker / invented-authority checks), and (c) rebuilds the answer line by line. Real model output is kept as a regression fixture.

**Fact-check** (`POST /fact-checks`): *cite-check* is deterministic (sections, Acts, cases, report citations vs the loaded sources, as-at date aware,
ambiguity reported). *Claim-check* needs a matter: the model extracts claims (each must be traceable to the draft) and judges them against the matter's own
documents; a verdict is kept only if a verbatim evidence quote is verified in the cited source, and claims whose figures/dates are missing from the
quote are downgraded to `partially_supported`. Wording is deliberate: "not found" = not in the sources available to Lygo, never "fabricated".
Results are stored firm-only (privileged work product). Limits: the public corpus is incomplete and the case-law corpus is not loaded, so most
case citations will read `not_found`; claim-check is not entailment-proof for paraphrased evidence; synchronous (~10 s, no job queue yet).
**Chronology** (`GET /matters/{id}/chronology`): deterministic from extracted dates + deadlines, each event linked to document/section/page.
**Case builder** (`POST /matters/{id}/case-builder`): issues / facts / law / arguments for and against / evidence gaps, same verified-citation pipeline,
confirmed matter facts included, advocate-only, kept as an advocate chat, always a draft.

## Email-less mode (decision: email verification disabled for now)
There was no registration-time email gate; the only artefact (`email_verified_at`) was removed (migration 0011). With no `SMTP_HOST` (or in local dev):
invitation responses carry `invite_link` (only to the authorised inviter) and `email_sent=false`; forgot-password sends nothing and creates nothing;
a platform admin issues audited, single-use reset links (`POST /platform/users/reset-link`) after verifying the person out of band; security alerts are skipped.
**Trade-off to accept:** an invitation link now travels through whatever channel the inviter uses, and anyone holding it who also controls an account
registered with the invited address can accept it; self-service password recovery does not exist. Both are bounded by: tokens are single use and expire
in 7 days / 30 minutes, the address must match the accepting account, MFA is never bypassed, and resets are operator-mediated. Re-enable email by setting `SMTP_HOST`.
