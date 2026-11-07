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

Licensing must be confirmed before any source is used in production (Kenya Law / NCLR content
is published under a non-commercial licence).
