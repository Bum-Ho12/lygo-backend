# Lygo Backend

Lygo is a multi-tenant legal data platform for law firms and legal consultants in Kenya.
It digitises legal documents (scanned paper via OCR, PDFs, Word, CSVs), organises them
into a secure per-firm data store, and runs two engines over them:

- **Rules Engine** (in-built, deterministic): clause detection, document flagging,
  deadline and obligation extraction, risk scoring. Explainable and testable.
- **AI Engine** (LLM + RAG + memory): legal research, case building, drafting support,
  plain-language explanations, and a client-facing chatbot — always grounded in cited sources.

Both engines share one retrieval layer (PostgreSQL + pgvector) partitioned into three
knowledge scopes: the **Public Law Corpus** (Constitution, Acts, regulations),
the **Firm Knowledge Base**, and **Client Documents**.

> Frontend lives in [`lygo-frontend`](https://github.com/Bum-Ho12/lygo-frontend).

## Tenancy at a glance

```
Platform (Lygo operators)
└── Firm  (tenant: law firm / consultant)          ← Firm Control Center
    ├── Members (partners, advocates, paralegals, admins)
    ├── Firm Knowledge Base (precedents, templates, research)
    └── Clients (sub-tenants)                       ← Client dashboards
        └── Matters (cases) → Documents, Flags, Chats, Reports
```

## Stack

| Concern | Choice |
|---|---|
| API | Python 3.12 (managed by [uv](https://docs.astral.sh/uv/)), FastAPI, Pydantic v2 |
| Database | PostgreSQL 16 + pgvector, Row-Level Security for tenant isolation |
| Background jobs | Celery + Redis (OCR, embedding, report generation) |
| Object storage | S3-compatible (RumptyCloud Object Storage by default; firm BYO supported) |
| OCR | Tesseract / PaddleOCR (self-hosted), pluggable cloud OCR adapter |
| LLM | Provider-agnostic interface (OpenAI-compatible endpoint, incl. RumptyCloud inference) |
| SMS | Thrivewave SMS API (behind an `SmsProvider` interface) |
| Hosting | RumptyCloud (VMs / Managed Kubernetes / Managed Postgres) |

## Repository layout

```
app/
  core/            config, security, tenancy context, logging
  api/v1/routers/  HTTP endpoints (one router per resource)
  domain/          business logic per bounded context (firms, clients, matters, documents…)
  db/              SQLAlchemy models, Alembic migrations, RLS policies
  ingestion/       OCR, parsers (PDF, DOCX, CSV, images), classification
  engine/
    rules/         deterministic flagging engine (models, evaluator)
    packs/         installable practice-area packs (pack.yaml, rules.yaml) + registry
    compare/       deterministic document comparison (alignment, diff, field changes)
    ai/            LLM providers, prompts, guardrails (citation checks, advice filter)
    rag/           chunking, embeddings, hybrid retrieval, reranking
    memory/        conversation + matter memory
    orchestrator/  routes a request across rules, RAG and LLM
  reports/         report & analytics generation (PDF/DOCX/CSV)
  storage/         storage abstraction + adapters (Lygo-managed, S3, Azure, GCS, SFTP)
  integrations/    sms (Thrivewave), email, outbound webhooks
  workers/         Celery tasks
corpus/            Public Law Corpus manifests (sources, versions, licences)
tests/             unit, integration, tenancy-isolation, AI evaluation
docs/              design documentation (start with docs/02-architecture.md)
```

## Documentation

| # | Doc | What it covers |
|---|---|---|
| 01 | [Vision & scope](docs/01-vision-and-scope.md) | Problem, solution, users, MVP scope |
| 02 | [Architecture](docs/02-architecture.md) | System diagram, services, request flows |
| 03 | [Multi-tenancy](docs/03-multi-tenancy.md) | Firm → client hierarchy, isolation, RBAC |
| 04 | [AI & Rules engines](docs/04-ai-engine.md) | Rules engine, RAG, memory, guardrails |
| 05 | [Ingestion pipeline](docs/05-ingestion-pipeline.md) | Two ingest endpoints, OCR, chunking |
| 06 | [Storage & BYO destinations](docs/06-storage-and-byo.md) | Lygo-hosted vs firm-provided storage |
| 07 | [Security & compliance](docs/07-security-and-compliance.md) | Kenya DPA, ODPC, Advocates Act, AI rules |
| 08 | [API design](docs/08-api-design.md) | Endpoint map, conventions |
| 09 | [Data model](docs/09-data-model.md) | Core entities and relations |
| 10 | [Notifications & SMS](docs/10-notifications-sms.md) | Thrivewave integration |
| 11 | [Deployment on RumptyCloud](docs/11-deployment-rumptycloud.md) | Environments, infra, residency |
| 12 | [Roadmap](docs/12-roadmap.md) | Phased delivery plan |
| 13 | [Testing strategy](docs/13-testing-strategy.md) | Tests, tenancy checks, AI evals |
| 14 | [Implementation plan & status](docs/14-implementation-plan.md) | Phases, defaults, known design fixes |
| — | [Open questions](docs/OPEN-QUESTIONS.md) | Decisions still needed |
| — | [ADRs](docs/adr/) | Architecture decision records |

## Getting started (local)

```bash
cp .env.example .env
docker compose up -d db redis minio
uv sync             # installs Python 3.12 (.python-version) and all dependencies into .venv
make dev            # API with reload: http://localhost:8000/docs
make test           # unit tests need no Docker
make migrate        # alembic upgrade head (owner role, DATABASE_MIGRATOR_URL)
make tenancy        # tenant-isolation tests (need Postgres; REQUIRE_DB=1 fails instead of skipping)
```

## Contributing / Security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
Never commit real client documents, credentials or personal data — use synthetic fixtures only.
