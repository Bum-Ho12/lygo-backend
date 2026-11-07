# Changelog

## [0.1.0] — 2026-11-07
- Project structure, design documentation, ADRs, local dev environment and app skeleton.

## Unreleased
- Foundations: config validation, PII-masking structured logging, problem+json errors, cursor pagination, request IDs, readiness endpoint.
- Practice-area pack system (ADR 0004) with draft `core_contract` and `insurance` rule packs and a review gate for production.
- Deterministic rules evaluator, clause-tree parser, and document comparison engine (ADR 0005).
- Database layer: Alembic, migration 0001 (identity, firms, clients, matters, append-only audit log), forced RLS with helper functions, `tenant_session`, and a 16-test tenant isolation suite run as the non-owner role.
- CI workflow (lint, types, security scan, unit tests, tenancy job), Makefile targets.
