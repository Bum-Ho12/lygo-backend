# Public Law Corpus

Manifests in `manifests/` describe each source loaded into the `public` scope.

```yaml
id: ke-constitution-2010
title: Constitution of Kenya, 2010
kind: constitution
source: <official source / URL>
version_date: 2010-08-27
licence: <confirmed licence — see docs/05-ingestion-pipeline.md>
checksum: sha256:...
chunking: statute   # statute | judgment | regulation
```

Load a version with `uv run python -m app.engine.rag.corpus_cli corpus/manifests/<id>.yaml path/to/text.txt`
(needs `LLM_BASE_URL` + `EMBEDDING_MODEL`), or `POST /v1/platform/corpus/documents` as a platform admin. Additional manifest keys:
`jurisdiction`, `effective_from`/`effective_to`, `licence_confirmed` (must be `true` to load in production). Loading a later version
automatically ends the validity of the version in force. Statutes should use `PART`/`1. Heading.`/`(1)` layout so sections become citations (`s.12`).

Licensing must be confirmed before any source is used in production (Kenya Law / NCLR content
is published under a non-commercial licence).
