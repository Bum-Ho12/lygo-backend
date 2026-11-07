# 11 — Deployment on RumptyCloud

## What RumptyCloud offers (Nov 2026)

RumptyCloud is a developer-focused cloud operated from Lagos, Nigeria, offering virtual
machines, managed Kubernetes, managed databases, object storage and AI inference behind an
OpenAI-compatible API. It currently runs **a single region (Lagos)**, with more zones and
regions planned.

Implications:
- **Data protection**: hosting Kenyan client data in Lagos is a cross-border transfer — see
  [07 §A.2](07-security-and-compliance.md). Plan safeguards and consent before go-live.
- **Resilience**: single region → keep encrypted off-site backups with a second provider/region
  and a tested restore runbook. Confirm Rumpty's SLA, backup and support terms in writing.
- **AI inference**: Rumpty's OpenAI-compatible endpoint can be the default LLM provider
  (keeps data with one processor). Confirm their data retention terms and model availability.
- **Latency**: Nairobi ↔ Lagos round trip is noticeable; use streaming responses and a CDN for the frontend.

## Environments

| Env | Purpose | Data |
|---|---|---|
| `local` | Developer machines (docker compose) | Synthetic only |
| `staging` | QA, demos | Synthetic only — never real client documents |
| `production` | Live | Real data, restricted access |

## Topology (production)

```
                ┌──────────── CDN / WAF ───────────┐
                │                                  │
        lygo-frontend (Next.js)            api.lygo.<domain>
                                                   │
                              ┌──────── Managed Kubernetes ────────┐
                              │ api (FastAPI, ≥2 replicas, HPA)    │
                              │ worker-ingest (OCR/parse)          │
                              │ worker-ai (embeddings, reports)    │
                              │ worker-notify (sms/email)          │
                              │ beat (scheduler)                   │
                              └───────┬───────────┬────────────────┘
                                      │           │
                        Managed Postgres 16    Redis
                        (+pgvector, PITR)
                                      │
                           Object storage (S3-compatible)
                                      │
                        Off-site encrypted backups (second provider)
```

Start smaller if needed: 2 VMs running docker compose + managed Postgres, moving to Kubernetes
when load requires.

## Deploy pipeline

1. PR → CI: lint (ruff), type check (mypy), tests incl. tenancy isolation, SAST, dependency scan.
2. Merge to `main` → build image → push to registry → deploy to staging (Helm or compose).
3. Tag `vX.Y.Z` → manual approval → production; Alembic migrations run as a pre-deploy job.
4. Rollback: previous image tag; migrations must be backward compatible (expand → migrate → contract).

## Configuration

All config via environment variables (see `.env.example`); secrets from a secrets manager in
production. Feature flags per firm stored in the DB.

## Observability

OpenTelemetry traces + metrics, structured JSON logs with PII masking, uptime checks, alerts on:
ingest backlog, worker failures, LLM error rate, DLR failure rate, storage-destination health,
RLS policy violations (should be zero).
