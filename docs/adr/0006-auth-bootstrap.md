# ADR 0006 — Identity tables sit outside tenant RLS

- Status: Proposed (identity tables are in migration 0001 without RLS; confirm when auth lands)
- Date: 2026-11-07

## Context
Login and membership lookup happen before a tenant is known, but ADR 0001 forces RLS on tenant tables.

## Decision (proposed)
`users`, `firm_memberships` and `client_memberships` are **not** firm-RLS tables. The app role gets
narrow grants: it can read a user's own memberships by user id and can never list across users.
Everything keyed by `firm_id` stays under forced RLS. Alternative considered: `SECURITY DEFINER`
lookup functions (more moving parts, harder to review).

## Consequences
- Queries on identity tables must always filter by the authenticated user; covered by tenancy tests.
- Revisit if platform-staff access patterns need row-level control.
