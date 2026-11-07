# 12 — Roadmap

## Phase 0 — Foundations (weeks 1–4)
- Repo setup, CI, environments on RumptyCloud.
- Auth, firms, memberships, clients, matters; RLS + tenancy tests.
- Storage abstraction with Lygo-managed adapter.
- Compliance groundwork: ODPC registration, DPA template, DPIA draft, NCLR licence request.

## Phase 1 — MVP for one practice area (weeks 5–14)
Pick one launch area (recommended: **immigration** *or* **insurance claims**).
- Both ingest endpoints, OCR, parsing, classification for that area's document types.
- Public Law Corpus: Constitution of Kenya 2010 + core Acts for the area
  (e.g. immigration: Kenya Citizenship and Immigration Act, 2011 and its Regulations, Refugees Act, 2021;
  insurance: Insurance Act, Cap. 487 and relevant regulations; plus Data Protection Act, 2019).
- Rules pack v1 for the area; flags UI.
- Advocate research chat with verified citations; client chatbot (explain mode).
- Firm Control Center v1: members, clients, AI settings, audit log.
- Thrivewave OTP + notifications.
- Reports: document review report, matter summary.
- Pilot with 2–3 firms.

## Phase 2 — Depth (months 4–6)
- Case builder, drafting from firm templates, approval mode.
- BYO storage: S3-compatible, Azure Blob, SFTP; scheduled exports; webhooks.
- Analytics dashboard; deadline reminders.
- Swahili for client chatbot and SMS.
- Second practice area.

## Phase 3 — Scale (months 7–12)
- Kenya-resident hosting option / hybrid.
- Dedicated DB per Enterprise firm; firm-hosted vector store.
- Case law corpus (licensed), "as at date" law versions.
- Email-in and scanner hot-folder integrations; mobile apps.
- AI Bill 2026 compliance package (model register, risk assessment exports).
