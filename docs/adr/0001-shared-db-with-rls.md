# ADR 0001 — Shared database with Row-Level Security for tenancy

- Status: Accepted
- Date: 2026-11-07

## Context
Many small and mid-size firms, each with many clients. We need strong isolation, low ops cost,
and an upgrade path for large firms.

## Options
1. Database per firm — strongest isolation, high ops cost early.
2. Schema per firm — migrations across hundreds of schemas get slow and error-prone.
3. Shared schema + `firm_id` + Postgres RLS — cheap, isolation enforced by the database.

## Decision
Option 3, with RLS **forced** on every tenant table and the app connecting as a non-owner role.
The tenancy resolver can map an Enterprise firm to a dedicated database later.

## Consequences
- Every query runs inside a transaction that sets `app.firm_id`/`app.client_id`.
- CI must include cross-tenant tests.
- Connection pooling must use transaction mode with `SET LOCAL` (no session-level state).
