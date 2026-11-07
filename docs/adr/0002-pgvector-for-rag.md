# ADR 0002 — pgvector in Postgres as the vector store

- Status: Accepted
- Date: 2026-11-07

## Context
RAG needs vector search over public law, firm knowledge and client documents, with the same
tenant isolation as relational data.

## Options
1. Dedicated vector DB (Qdrant, Weaviate, Pinecone) — separate isolation model, extra sub-processor.
2. pgvector in the main Postgres — one database, RLS applies to chunks, hybrid search with full-text in one query.

## Decision
pgvector with HNSW indexes, hybrid retrieval (vector + `tsvector`) and reranking. Retrieval code
goes through `app/engine/rag/retrieval` so a dedicated vector DB or firm-hosted store can be added
behind the same interface.

## Consequences
- Watch index size; partition `chunks` by scope (and by firm for large tenants) when needed.
- Re-embedding on model change is a background migration.
