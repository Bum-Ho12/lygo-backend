# 15 — Vendor DPA questionnaire (sub-processors)

> Working document, not legal advice. Send to each vendor; keep their written answers with the signed DPA. Have a Kenyan
> data-protection advocate review the contracts. Lygo is the *processor* for firms' data, so every vendor below is a *sub-processor*.

## A. Data-flow inventory (what the code actually sends)
| Vendor | Receives | Never receives | Trigger |
|---|---|---|---|
| **Anthropic** (chat LLM) | Question text; up to 6-10 retrieved passages (<= 2,400 chars each) from the user's own matter/firm/public sources; last 8 turns (<= 1,500 chars each); confirmed matter facts; conversation summaries; fact-check drafts (<= 20,000 chars) and evidence passages; document summaries (first 6 passages) | Whole files, national IDs (not extracted), credentials, other firms' data | Every chat, research, case-builder, summary, fact-check |
| **Voyage AI** (embeddings) | Full text of every indexed passage (client documents, firm knowledge, public law) and every search query | Files, metadata, user identities | On ingestion/re-index and each search |
| **Thrivewave** (SMS) | Kenyan mobile numbers; OTP codes; fixed-template nudges with the firm name | Case details (templates forbid them) | Phone verification, SMS login factor, notifications |
| **Email provider** (when enabled) | Recipient address, firm name, invite/reset links | Case details (templates forbid them) | Invitations, resets, alerts |
| **RumptyCloud** (hosting) | Everything at rest: database, object storage, backups | n/a | Always |

## B. Questions for every vendor (get answers in writing)
1. **Role and contract.** Will you sign a DPA naming you as our processor/sub-processor? Share the current text and sub-processor list.
2. **Training.** Is our content used to train or improve any model, now or in future, under any plan/tier? Is that contractually excluded?
3. **Retention.** How long are inputs and outputs stored (including logs, abuse monitoring, backups)? Is zero/short retention available, and for which endpoints/features? Are there exceptions (e.g. uploaded files)?
4. **Location.** Where is data processed and stored? Can we pin a region? Which sub-processors do you use?
5. **Cross-border.** What transfer mechanism covers Kenya -> your region (Kenyan Standard Clauses / equivalent safeguards)? Will you complete a transfer assessment with us?
6. **Security.** Certifications (SOC 2 / ISO 27001), encryption in transit/at rest, access controls, staff access to customer content, pen-test cadence.
7. **Breach notice.** Notification time to us (target <= 24-48 h so we can meet the ODPC 72-hour rule) and contact route.
8. **Data-subject requests.** How do you help us access, correct or delete an individual's data? Turnaround?
9. **Deletion.** Deletion/return on termination, with a certificate; deletion on request for a given period.
10. **Audit.** Audit rights or independent reports; notice of sub-processor changes with a right to object.
11. **Government access.** Policy on disclosure requests; notice to us where lawful.
12. **Sensitive data.** Any restrictions on special-category data (health, children, biometrics)? Our clients' files contain such data.

## C. Vendor-specific
- **Anthropic.** Confirm the API key belongs to a *commercial* organisation (not an individual plan). Get the DPA and the no-training clause for that account type; ask about zero data retention for privileged legal material and which features it covers; ask for the retention period for our plan. Official reference: platform.claude.com -> API and data retention.
- **Voyage AI.** We found no public DPA or retention terms. Ask explicitly: training opt-out (and that it is enforced for our account), retention, DPA, region. **If answers are inadequate:** self-host an open-weight embedding model on our own infrastructure (changing models requires re-indexing).
- **Thrivewave.** DPA; message-log retention; data location; callback signing (currently unsigned); sender-ID registration; token lifetime.
- **Email provider.** DPA; region; sending-domain requirements (SPF/DKIM/DMARC); log retention.
- **RumptyCloud.** SLA, backups, retention of AI-inference data if used, DPA + Kenyan Standard Clauses (single Lagos region = cross-border), exit/export.

## D. Minimum acceptable terms (proposal for sign-off)
No training on our data; deletion/retention stated and <= 30 days for inputs where available; breach notice <= 48 h; named regions plus a transfer mechanism; sub-processor change notice; DPA signed before production data is sent. **Until a vendor meets these, send synthetic data only.**

## E. Tracking
| Vendor | DPA requested | DPA signed | Training excluded | Retention confirmed | Transfer basis | Added to sub-processor list |
|---|---|---|---|---|---|---|
| Anthropic | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Voyage AI | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Thrivewave | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| Email provider | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
| RumptyCloud | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |
