# 07 — Security & Compliance

> This is an engineering checklist, not legal advice. Have a Kenyan advocate (data protection
> and professional-conduct specialist) review it before launch.

## A. Legal & regulatory issues found in research (Nov 2026)

### 1. Data Protection Act, 2019 and Regulations, 2021 (ODPC)

- **Roles**: the law firm is the *data controller*; Lygo is the *data processor* (and controller
  for its own account/billing data). Sign a Data Processing Agreement with every firm.
- **Registration**: Lygo must register with the ODPC as a data processor (and controller).
  Firms register themselves; the Control Center can remind them.
- **DPIA**: legal matter data at scale + AI profiling → do a Data Protection Impact Assessment
  before launch and keep it updated.
- **Sensitive personal data** is common in legal files (health, family details, ethnicity,
  biometrics, children). Tag it at ingest (`sensitivity=sensitive`) and apply stricter access,
  logging and transfer rules.
- **Breach notification**: notify the ODPC within 72 hours of becoming aware of a breach and
  notify affected data subjects. Lygo must notify the firm (controller) without delay — build
  the incident runbook around this.
- **Data subject rights**: access, correction, deletion, objection — firms need tooling to
  service requests about their clients (export + delete per client).

### 2. Cross-border transfers — critical because RumptyCloud is in Lagos, Nigeria

- RumptyCloud currently runs one region in Lagos. The ODPC's *Guidance Notes for Cross-border
  Data Transfers* (published 8 September 2026) state that processing personal data in a cloud
  with servers outside Kenya **is** a cross-border transfer.
- A transfer needs a basis: appropriate safeguards (the guidance includes Kenyan Standard
  Clauses for controller→controller and controller→processor), adequacy, necessity, or consent.
  A documented **transfer impact assessment** is expected.
- **Sensitive personal data** may only be processed outside Kenya with the data subject's
  **consent** and confirmation of appropriate safeguards (DPA s.49).
- **Onward transfers** for the recipient's own purposes (analytics, product improvement,
  marketing) are prohibited — so LLM/OCR/SMS vendors must not retain or train on Lygo data.
- **Data localisation** applies to "strategic interests of the State" categories (e.g. civil
  registration, elections, certain public finance, healthcare) — generally not law-firm data,
  but check for government/public-sector clients.

**Actions**
1. Sign Kenyan Standard Clauses with RumptyCloud (and every sub-processor); keep a sub-processor list.
2. Write and keep a transfer impact assessment.
3. Collect explicit consent for out-of-Kenya processing of sensitive data in the client portal
   onboarding flow (firm-configurable wording), and record it (`consents` table).
4. Offer a **Kenya-resident option**: BYO storage in a Kenyan data centre, and plan a Kenyan
   hosting region (or hybrid: Kenyan storage + Lagos compute) for firms that require it.
5. Encrypt at rest with keys Lygo controls (and per-firm keys) as a supplementary safeguard.

### 3. Advocates Act (Cap. 16) and Law Society of Kenya

- **Section 34** prohibits unqualified persons from drawing or preparing certain documents
  (e.g. conveyancing instruments, documents relating to legal proceedings), with an exemption
  for persons employed by an advocate acting within that employment.
  → Drafting and case-building tools are **firm-side tools used under advocates' supervision**.
  → The client chatbot **explains** documents and the law; it does not draft instruments or
    pleadings for clients and does not present itself as a lawyer (also see s.33 on pretending
    to be qualified).
- **Professional confidentiality / privilege**: treat all firm data as privileged; no Lygo staff
  access without a firm-granted, time-limited, audited support session.
- LSK has formed an ICT & AI committee and its 2026 annual conference focused on AI in legal
  practice — expect professional guidance; track and adapt.

### 4. Judiciary AI policy and fake citations

Kenyan courts have warned advocates about AI-generated filings with fabricated authorities and
the Judiciary has circulated an AI policy with draft practice directions. Product response:
citation verification guardrail, "verified source" badges, and every drafted document marked as
a draft requiring advocate review. Track the final practice directions.

### 5. AI-specific law

- Kenya National AI Strategy 2025–2030 (March 2025).
- **Artificial Intelligence Bill, 2026** (published; proposes a risk-based framework and an AI
  Commissioner). Legal-services AI may fall in a higher-risk tier — keep a model register,
  risk assessment, human-oversight records and transparency notices so compliance is cheap later.

### 6. Other

- Computer Misuse and Cybercrimes Act, 2018 — security incident handling.
- Consumer Protection Act, 2012 — clear terms, no misleading claims about the AI.
- Communications Authority rules for SMS: registered sender ID, opt-out for promotional SMS
  (see [10](10-notifications-sms.md)).
- Kenya Law content licence for the Public Law Corpus (see [05](05-ingestion-pipeline.md)).

## B. Security controls

| Area | Control |
|---|---|
| Identity | Email/phone + password with Argon2id, TOTP MFA (required for firm roles), OIDC SSO (Microsoft/Google) for firms, short-lived JWT access tokens + rotating refresh tokens |
| Authorisation | RBAC + matter assignment + RLS in Postgres (defence in depth) |
| Encryption | TLS 1.2+ everywhere; AES-256 at rest; per-firm data keys (envelope encryption); field-level encryption for ID numbers |
| Secrets | Vault/KMS; no secrets in env files in production |
| Files | AV scan, MIME sniff, isolated OCR workers, pre-signed URLs with short TTL |
| Logging | Structured logs with PII masking; immutable audit log (who/what/when/tenant) |
| AI | Prompt-injection defences, output guardrails, no training on customer data, vendor zero-retention terms |
| AppSec | OWASP ASVS L2 target, dependency scanning, SAST in CI, annual pen test |
| Availability | Daily encrypted backups + PITR, restore drills quarterly |
| People | Least privilege, break-glass access with approval and alerting |

## C. Pre-launch compliance checklist

- [ ] ODPC registration (processor + controller)
- [ ] DPIA completed and signed off
- [ ] DPA template for firms; sub-processor list published
- [ ] Kenyan Standard Clauses with RumptyCloud, LLM provider, Thrivewave, email provider
- [ ] Transfer impact assessment documented
- [ ] Consent flow for sensitive data / cross-border processing
- [ ] Kenya Law / NCLR content licence confirmed
- [ ] Client chatbot wording reviewed by an advocate (information, not advice)
- [ ] Breach response runbook (72-hour ODPC timeline) tested
- [ ] Penetration test passed
