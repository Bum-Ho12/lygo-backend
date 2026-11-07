# 13 — Testing Strategy

| Layer | Tooling | What |
|---|---|---|
| Unit | pytest | Domain services, rule evaluation, chunkers, parsers, prompt rendering |
| Rules fixtures | pytest + YAML fixtures | Each rule has positive/negative sample text; packs reviewed by an advocate |
| Integration | pytest + testcontainers (Postgres+pgvector, Redis, MinIO) | API ↔ DB ↔ storage ↔ workers |
| **Tenancy isolation** | `tests/tenancy/` | Two firms × two clients; every endpoint and retrieval call asserted to return no foreign rows; runs with the real non-owner DB role |
| AI evaluation | `tests/eval/` golden sets | Citation accuracy, faithfulness, abstain-when-unknown, advice-boundary refusals, reading level |
| Contract | schemathesis on OpenAPI | Fuzz request validation |
| Security | bandit, pip-audit, ZAP baseline | SAST, dependencies, DAST on staging |
| Load | k6 | Chat concurrency, ingest throughput |

Rules:
- No real client data in tests or fixtures — synthetic documents only (`tests/fixtures/`).
- Any change to prompts, model, chunking or retrieval must run `make eval` and attach results to the PR.
- Coverage target 80% for `app/domain`, `app/engine/rules`, `app/core`.
