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

## Implementation notes (2026-11)
- `chunks` carries `firm_id`/`client_id`/`scope`/`shared_with_clients` and the same RLS as other tenant tables, plus read-only policies for
  public law (everyone) and knowledge shared with clients; only platform roles can write public chunks.
- Hybrid retrieval = cosine over HNSW + `ts_rank_cd` over a generated `tsvector`, fused with RRF (k=60).
- Vector dimension is fixed at 1024 in migration 0006; the indexer refuses vectors of another size.

## Consequences
- Watch index size; partition `chunks` by scope (and by firm for large tenants) when needed.
- Re-embedding on model change is a background migration.
