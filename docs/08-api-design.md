# 08 — API Design

## Conventions

- Base path `/v1`. JSON, `snake_case`. OpenAPI generated at `/docs` (disabled in production for anonymous users).
- Tenant is explicit in the path: `/v1/firms/{firm_id}/...`. The server **also** checks the
  caller's membership — the path is never trusted alone.
- Auth: `Authorization: Bearer <access_token>`. Machine access: per-firm API keys with scopes.
- Pagination: cursor-based `?cursor=&limit=` → `{ items, next_cursor }`.
- Errors: RFC 9457 problem+json `{ type, title, status, detail, code }`.
- Idempotency: `Idempotency-Key` header on POSTs that create resources or send messages.
- Long jobs return `202 Accepted` + `job_id`; progress via `GET /v1/jobs/{id}` or SSE.
- Streaming chat: Server-Sent Events.

## Endpoint map

### Auth & identity
```
POST   /v1/auth/register            POST /v1/auth/login        POST /v1/auth/refresh
POST   /v1/auth/mfa/verify          POST /v1/auth/otp/sms      POST /v1/auth/logout
GET    /v1/me                       GET  /v1/me/memberships    (firms & clients I belong to)
```

### Platform console (platform roles only)
```
GET/POST   /v1/platform/firms               PATCH /v1/platform/firms/{firm_id}
GET        /v1/platform/usage               GET   /v1/platform/health
GET/POST   /v1/platform/corpus/sources      POST  /v1/platform/corpus/sources/{id}/sync
```

### Firm Control Center
```
GET/PATCH  /v1/firms/{firm_id}                         (profile, branding, AI settings)
GET/POST   /v1/firms/{firm_id}/members                 PATCH/DELETE .../members/{user_id}
POST       /v1/firms/{firm_id}/invitations
GET/POST   /v1/firms/{firm_id}/storage-destinations    POST .../{dest_id}/test
GET/PUT    /v1/firms/{firm_id}/storage-policy
GET/PATCH  /v1/firms/{firm_id}/rule-packs              POST .../rules
GET        /v1/firms/{firm_id}/audit-log
GET        /v1/firms/{firm_id}/usage
POST       /v1/firms/{firm_id}/exports                 (full or per-client export job)
GET/POST   /v1/firms/{firm_id}/api-keys
GET/POST   /v1/firms/{firm_id}/webhooks
```

### Implemented so far (E3 + email-less mode)
`POST/GET /firms/{id}/fact-checks`, `GET /fact-checks/{id}`, `GET /matters/{id}/chronology?after=&before=`, `POST /matters/{id}/case-builder`,
`POST /platform/users/reset-link`. Invitation responses include `invite_link` when `email_sent` is false.

### Implemented so far (auth hardening)
`POST /auth/login` (returns tokens, or `mfa_required` + `mfa_token`), `/auth/mfa/sms/send`, `/auth/mfa/verify` (`totp` | `sms` | `recovery`),
`/auth/password/forgot`, `/auth/password/reset`; account security under `/me`: `/security`, `/phone`, `/phone/verify`, `/mfa/sms`,
`/mfa/totp/{enroll,confirm,disable}`, `/mfa/recovery-codes`, `/password`. Invitations: `GET /firms/{id}/invitations`, `POST .../{id}/resend`.
Errors: `429 too_many_attempts` (with `Retry-After`), `403 mfa_required`.

### Implemented so far (Phase E2, AI)
`/firms/{id}/chats` (POST/GET), `/chats/{id}` (GET), `/chats/{id}/messages` (POST; JSON or `Accept: text/event-stream`),
`/ai-outputs` (GET pending) and `/ai-outputs/{id}:approve` (POST), `/ai-settings` (GET/PATCH), `/matters/{id}/research` (POST),
`/matters/{id}/facts` (GET/POST/PATCH), `/documents/{id}/summary` (POST).

### Implemented so far (Phase A/B)
`/auth/{register,login,refresh,logout}`, `/me`, `/me/memberships`, `/invitations/accept`,
`/platform/firms` (GET/POST), `/firms/{id}` (GET/PATCH), `/members` (GET, PATCH, DELETE), `/invitations` (POST),
`/clients` (CRUD + `/invitations`), `/clients/{id}/matters`, `/matters/{id}` (GET/PATCH), `/matters/{id}/deadlines`
(GET/POST/PATCH), `/tasks` (GET/POST/PATCH). Firm-only resources (members, tasks, invitations) return 403 to client users.

### Clients & matters
```
GET/POST   /v1/firms/{firm_id}/clients                 GET/PATCH/DELETE .../clients/{client_id}
POST       /v1/firms/{firm_id}/clients/{client_id}/invitations   (portal users)
GET/POST   /v1/firms/{firm_id}/clients/{client_id}/matters
GET/PATCH  /v1/firms/{firm_id}/matters/{matter_id}
GET/POST   /v1/firms/{firm_id}/matters/{matter_id}/deadlines
GET/PATCH  /v1/firms/{firm_id}/matters/{matter_id}/facts           (matter fact sheet)
```

### Ingest endpoints (the two doors)
```
# Firm knowledge base
POST   /v1/firms/{firm_id}/knowledge/documents           (multipart | presigned)
POST   /v1/firms/{firm_id}/knowledge/uploads:presign
GET    /v1/firms/{firm_id}/knowledge/documents           PATCH .../{doc_id} (tags, share_with_clients)

# Client documents
POST   /v1/firms/{firm_id}/clients/{client_id}/documents (multipart | presigned; optional matter_id)
POST   /v1/firms/{firm_id}/clients/{client_id}/uploads:presign
GET    /v1/firms/{firm_id}/clients/{client_id}/documents

# Shared
GET    /v1/firms/{firm_id}/documents/{doc_id}            (metadata, status timeline)
GET    /v1/firms/{firm_id}/documents/{doc_id}/content    (presigned URL)
GET    /v1/firms/{firm_id}/documents/{doc_id}/flags
PATCH  /v1/firms/{firm_id}/flags/{flag_id}               (acknowledge, dismiss with reason)
POST   /v1/firms/{firm_id}/documents/{doc_id}:reprocess
GET    /v1/firms/{firm_id}/review-queue                  (low-confidence OCR pages)
```

### AI
```
POST   /v1/firms/{firm_id}/chats                         { audience, matter_id?, client_id? }
POST   /v1/firms/{firm_id}/chats/{chat_id}/messages      (SSE stream; returns citations)
GET    /v1/firms/{firm_id}/chats/{chat_id}
POST   /v1/firms/{firm_id}/matters/{matter_id}/research   { question }
POST   /v1/firms/{firm_id}/matters/{matter_id}/case-builder
POST   /v1/firms/{firm_id}/matters/{matter_id}/drafts     { template_id, instructions }
POST   /v1/firms/{firm_id}/ai-outputs/{id}:approve        (approval mode)
GET    /v1/corpus/search?q=                              (public law search)
GET    /v1/corpus/documents/{id}/sections/{section}
```

### Reports & analytics
```
POST   /v1/firms/{firm_id}/reports                       { type, matter_id?, format: pdf|docx|csv|json }
GET    /v1/firms/{firm_id}/reports/{report_id}
GET    /v1/firms/{firm_id}/analytics/overview            (matters, flags by severity, deadlines, workload)
```

### Notifications
```
GET    /v1/me/notifications           PATCH /v1/me/notification-preferences
POST   /v1/integrations/thrivewave/delivery-reports      (inbound DLR webhook, signature checked)
```

## Outbound webhooks (to firms)

Events: `document.processed`, `document.flagged`, `matter.deadline_due`, `report.ready`,
`export.completed`. Signed with HMAC-SHA256 (`X-Lygo-Signature`), retried with exponential backoff.

## Client-portal access

Client users call the same routes; RLS + `client_isolation` restricts them to their own client.
Routes that are firm-only (knowledge base writes, member management, rule packs, drafts) check
for a firm role and return 403 for client roles.
