# 16 — Firm-facing Data Processing Agreement: outline

> Outline for a Kenyan advocate to turn into the contract. Not legal advice. Statutory references are placeholders to be confirmed by
> counsel against the Data Protection Act 2019, the 2021 Regulations and current ODPC guidance (see docs/07).

**Parties.** The law firm (*Controller*) and Lygo (*Processor*). Lygo is controller only for its own account/billing data.

## Main terms
1. **Definitions and roles** — personal data, special-category data, data subjects (firm's clients and their counterparties), sub-processors.
2. **Subject matter, duration, nature and purpose** — hosting, OCR/parsing, flagging, search, AI-assisted research and explanation, notifications; for the term of the services only.
3. **Instructions** — processing only on the firm's documented instructions (the contract + settings in the Control Center); Lygo tells the firm if an instruction appears unlawful.
4. **Confidentiality and privilege** — all firm data treated as privileged; staff bound by confidentiality; **no Lygo access to content** except a firm-granted, time-limited, audited support session (docs/03).
5. **Security** — Annex B measures; MFA for staff; encryption; tenant isolation; logging.
6. **Sub-processors** — general authorisation to those in Annex C; 30 days' notice of changes; right to object; Lygo remains liable; flow-down of equivalent obligations.
7. **International transfers** — what leaves Kenya and where (Annex C); the transfer mechanism (Kenyan Standard Clauses / safeguards); firm obtains **consent for sensitive data** processed outside Kenya where required; data-residency options (BYO storage in Kenya).
8. **AI-specific terms** — no use of firm data to train models by Lygo or its AI vendors; AI outputs are drafts requiring advocate review; client-visible answers subject to the firm's approval setting; model/prompt-version record-keeping.
9. **Data-subject requests** — Lygo assists within [5] business days (export/delete per client).
10. **Personal-data breach** — Lygo notifies the firm **without undue delay and within [24-48] hours** with the information needed for the firm's ODPC notice (72-hour rule) and data-subject notices.
11. **Deletion and return** — export on exit, then deletion and crypto-shredding of the firm key; certificate; retention of minimised audit records only as required by law.
12. **Audit and information** — records of processing, independent reports, reasonable audits on notice; support for the firm's DPIA.
13. **Liability, term, governing law (Kenya), order of precedence** with the master services agreement.

## Annex A — Details of processing
| Item | Content |
|---|---|
| Data subjects | Firm members; clients (individuals, business contacts); counterparties named in documents |
| Data types | Identity and contact data, document content, matter data, chat content, audit logs; may include special-category data (health, children, biometrics) |
| Not extracted | National ID numbers and similar identifiers are not extracted into structured fields |
| Frequency / retention | Continuous during the term; per the firm's retention settings; deletion on exit |

## Annex B — Technical and organisational measures
Row-level security isolating every firm and client in the database; encryption in transit and at rest; per-firm data keys wrapped by a master key (crypto-shredding on exit); MFA mandatory for staff (authenticator app preferred); rate limiting and lockout; malware scanning of uploads; append-only audit log; least-privilege access; backups with tested restores; documented incident runbook. *(Update this annex to match what is actually deployed at signing.)*

## Annex C — Sub-processors and transfers
| Sub-processor | Purpose | Data | Location | Mechanism |
|---|---|---|---|---|
| RumptyCloud | Hosting | All | Lagos, Nigeria | [Kenyan Standard Clauses] |
| Anthropic | AI answers | Question + retrieved excerpts | [US / confirm] | [confirm] |
| Voyage AI | Embeddings | Passage and query text | [US / confirm] | [confirm] |
| Thrivewave | SMS | Mobile numbers, OTP/notices | [confirm] | [confirm] |
| Email provider | Invitations, resets | Addresses, links | [confirm] | [confirm] |
| Firm's own storage (optional) | BYO storage | Originals | Firm's choice | Firm's responsibility |

## Annex D — Firm responsibilities
Lawful basis and notices to its clients; consent where required; configuring AI settings (client assistant off by default, approval on by default); reviewing AI outputs; registering with the ODPC as a controller.

## Open decisions before drafting
Breach-notice and assistance deadlines; liability cap; whether to offer a Kenya-only data option at launch; sub-processor objection process; support-access procedure; retention defaults.
