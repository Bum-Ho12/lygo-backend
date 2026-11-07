# 01 — Vision & Scope

## Problem statement

Kenyan law firms and legal consultants handle large volumes of documents across immigration,
insurance, conveyancing, employment, family and commercial matters. Much of it is still paper,
and the rest is scattered across email, shared drives and personal laptops.

- Advocates spend hours locating, reading and re-typing information from files and statutes.
- Precedents and past work inside a firm are hard to find and reuse.
- Clients do not understand their own documents (contracts, policies, permits, notices) and
  call or visit the firm for routine explanations and status updates.
- Risky clauses, missing information and deadlines are spotted late, or missed.
- Data protection obligations (Kenya Data Protection Act, 2019) are hard to meet with ad-hoc tools.

## Solution statement

Lygo is a multi-tenant legal data platform. Each firm gets a secure workspace where:

1. Documents enter through two endpoints — **firm knowledge** and **client documents** — from
   scanners (OCR), uploads, email or API.
2. A **Rules Engine** flags clauses, gaps, deadlines and risks deterministically.
3. An **AI Engine** with RAG over the Public Law Corpus, the firm's knowledge base and the
   matter's documents supports research, case building and drafting — with citations.
4. Clients get their own dashboard inside the firm's workspace with a plain-language chatbot
   and colour-coded flags on their documents, so they understand what they signed.
5. Firms choose where processed data lives: hosted by Lygo, or pushed to their own storage.

## Users

| Persona | Where they work | Main jobs |
|---|---|---|
| Platform operator (Lygo staff) | Platform Console | Onboard firms, billing, corpus updates, support, health |
| Firm owner / admin | Firm Control Center | Users & roles, clients, storage destinations, billing, audit, AI settings |
| Advocate / partner | Firm Workspace | Matters, research, case building, drafting, review flags, approve client-facing output |
| Paralegal / clerk | Firm Workspace | Scanning, uploading, tagging, data entry review |
| Client (individual or business) | Client Portal | Upload documents, see flags explained, ask the chatbot, track matter status |

## MVP scope (Phase 1)

In scope:
- Firm onboarding, members, roles; client sub-workspaces; matters.
- Firm and client document ingest (PDF, DOCX, images/scans with OCR, CSV).
- Public Law Corpus: Constitution of Kenya 2010 plus a first set of Acts relevant to the
  launch practice area (see [Roadmap](12-roadmap.md)).
- Rules Engine with one practice-area rule pack.
- RAG research chat for advocates (citations required); plain-language client chatbot.
- Document flags with explanations.
- Basic reports (matter summary, document review report).
- Lygo-hosted storage plus one BYO adapter (S3-compatible).
- SMS notifications via Thrivewave; email notifications.

Out of scope for MVP: court e-filing integration, billing/time-tracking, e-signatures,
public (non-client) consumer chatbot, mobile apps, Swahili UI (planned Phase 2).

## Principles

1. **Grounded or silent** — the AI never states law without a retrievable citation.
2. **Advocate in the loop** — anything that could be legal advice to a client is reviewed or
   framed as information; drafting of legal instruments is a firm-side tool for advocates.
3. **Isolation by default** — one firm's data can never reach another firm's prompts, indexes or reports.
4. **Firm owns the data** — the firm is the data controller; Lygo is the processor.
5. **Explainable flags** — every flag links to the rule or source that produced it.
