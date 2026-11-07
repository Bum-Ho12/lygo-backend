# ADR 0003 — Separate deterministic Rules Engine and AI Engine

- Status: Accepted
- Date: 2026-11-07

## Context
Document flagging must be explainable and consistent; research and explanation need
language understanding.

## Decision
- **Rules Engine** (YAML rule packs + Python hooks) produces flags, deadlines and risk scores.
  Same input → same output, versioned, testable, reviewed by advocates.
- **AI Engine** (LLM + RAG + memory) explains flags, answers questions and supports research and
  drafting, always with citations.
- An orchestrator combines them: rules output is passed to the LLM as structured context; the LLM
  can *suggest* new flags, shown as "AI-suggested" until an advocate confirms.

## Consequences
- Two places to maintain, but flags stay auditable and cheap, and AI cost is limited to user requests.
