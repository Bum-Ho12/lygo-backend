# 03 — Multi-tenancy

## Hierarchy

```
platform
└── firm            (tenant)            firm_id
    ├── member      (firm user)         user_id + firm_membership(role)
    ├── knowledge   (firm KB scope)
    └── client      (sub-tenant)        client_id  (individual or business)
        ├── client_user (portal login)  user_id + client_membership(role)
        └── matter                      matter_id
            └── document, flag, chat, report, task
```

A single person can be a member of several firms (e.g. a consultant) or a client of several
firms. Identity (`users`) is global; access is always through a membership row.

## Isolation model

Shared database, shared schema, **Row-Level Security** on every tenant-owned table.

- Every tenant-owned table has `firm_id NOT NULL`; client-scoped tables also have `client_id`.
- On each request the API opens a transaction and runs:
  ```sql
  SET LOCAL app.firm_id = '<uuid>';
  SET LOCAL app.client_id = '<uuid or empty>';
  SET LOCAL app.role = 'advocate' | 'client' | ...;
  ```
- Policies (sketch):
  ```sql
  ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
  ALTER TABLE documents FORCE ROW LEVEL SECURITY;
  CREATE POLICY firm_isolation ON documents
    USING (firm_id = current_setting('app.firm_id')::uuid);
  CREATE POLICY client_isolation ON documents AS RESTRICTIVE
    USING (current_setting('app.client_id', true) = ''
           OR client_id = current_setting('app.client_id')::uuid);
  ```
- The application DB role is **not** the table owner and has no `BYPASSRLS`.
- Vector chunks (`chunks` table) carry the same `firm_id`/`client_id`/`scope` columns and the
  same policies, so retrieval cannot leak across tenants even if a query filter is forgotten.
- Public Law Corpus chunks have `scope='public'` and `firm_id IS NULL`, readable by all.
- Background workers receive `firm_id` in the task payload and set the same session variables.

Upgrade path: a firm on an Enterprise plan can get a **dedicated database** (or schema) — the
tenancy resolver maps `firm_id → connection`. Same code, different DSN.

## Knowledge scopes

| Scope | Owner | Who can retrieve |
|---|---|---|
| `public` | Platform | Everyone |
| `firm_kb` | Firm | Firm members; clients only for items the firm marks `shared_with_clients` |
| `client` | Firm, about a client | Firm members with access to that client; that client's users |
| `matter` | Sub-scope of `client` | Members assigned to the matter (if matter-level restriction is on); the client |

## Roles (RBAC)

| Role | Scope | Key permissions |
|---|---|---|
| `platform_admin` | Platform | Manage firms, plans, corpus; **no** access to firm content by default (break-glass only, audited) |
| `platform_support` | Platform | Read firm metadata, health; content access only via firm-granted support session |
| `firm_owner` | Firm | Everything in the firm incl. billing, storage destinations, data export/deletion |
| `firm_admin` | Firm | Users, clients, settings, audit log |
| `advocate` | Firm | Matters, research, drafting, approve client-facing outputs |
| `paralegal` | Firm | Upload, tag, OCR review; no approvals |
| `viewer` | Firm | Read-only |
| `client_admin` | Client | Manage own client users (for business clients), all client matters |
| `client_user` | Client | Own matters: upload, view flags, chat |

Permissions are checked in `app/core/security.py` with a `require(permission)` dependency;
fine-grained checks (e.g. matter assignment) live in domain services.

## Firm Control Center capabilities (backend)

- Members & invitations, roles, MFA enforcement.
- Clients: create, invite portal users, archive.
- Storage destinations (see [06](06-storage-and-byo.md)), data residency choice.
- AI settings: enabled features, client chatbot on/off, reading level, require advocate
  approval for client-visible AI answers, custom firm system prompt.
- Rule packs: enable packs, adjust thresholds, add firm-specific rules.
- Branding for client portal (logo, colours, custom domain).
- Audit log, usage & billing, data export, retention policies.

## Testing isolation

`tests/tenancy/` contains tests that create two firms and two clients each and assert that every
list/read/search/retrieval endpoint returns zero rows from the other tenants. These run in CI on
every PR and block merges.
