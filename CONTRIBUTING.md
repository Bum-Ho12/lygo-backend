# Contributing

## Workflow
- Branch from `main`: `feat/<short-name>`, `fix/<short-name>`, `docs/<short-name>`.
- Conventional commits (`feat: add client ingest endpoint`).
- Open a PR; CI must pass (lint, types, tests, **tenancy isolation**, security scan).
- One approving review; two for changes to `app/core/security.py`, `app/core/tenancy.py`,
  `app/db/` policies, or `app/engine/ai/guardrails/`.

## Code rules
- Routers are thin; business logic lives in `app/domain` or `app/engine`.
- Every new tenant-owned table: `firm_id NOT NULL`, RLS policy in the same migration, tenancy test.
- No direct SQL without the tenancy session (use `get_tenant_session`).
- No real client data anywhere in the repo, logs, tests or screenshots.
- Prompt or model changes: run `make eval` and paste the summary into the PR.
- Architecture changes: add an ADR in `docs/adr/`.

## Local commands
```bash
make dev        # run API with reload
make test       # all tests
make tenancy    # isolation tests only
make eval       # AI evaluation suite
make lint       # ruff + mypy
```
