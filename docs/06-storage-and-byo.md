# 06 — Storage & Bring-Your-Own Destinations

Firms choose where their data lives after processing. Lygo supports two modes, per firm:

| Mode | Originals | Processed data (text, chunks, embeddings, reports) | Lygo DB keeps |
|---|---|---|---|
| **Hosted** (default) | Lygo object storage | Lygo Postgres + object storage | everything |
| **BYO** | Firm's storage | Firm's storage (exports) and/or Lygo index | metadata + index (configurable) |

In BYO mode the firm decides which artefact classes go where:

```yaml
# storage policy (per firm, editable in Control Center)
primary_destination: firm-s3-nairobi
artefacts:
  originals:   { destination: firm-s3-nairobi }
  ocr_text:    { destination: firm-s3-nairobi }
  embeddings:  { destination: lygo-index, retain_in_lygo: true }   # needed for search
  reports:     { destination: firm-s3-nairobi, also_keep_copy: false }
  chat_logs:   { destination: lygo-db, retention_days: 365 }
exports:
  - { destination: firm-sftp, schedule: daily, include: [reports, matter_summaries] }
```

Note: semantic search needs an index somewhere. Options are (a) Lygo keeps the vector index
(chunk text can be encrypted with a firm key), or (b) Enterprise: firm provides its own
pgvector / vector DB endpoint and Lygo queries it per request.

## Adapters — `app/storage/adapters`

Common interface (`app/storage/base.py`):

```python
class StorageDestination(Protocol):
    async def put(self, key: str, data: AsyncIterator[bytes], *, content_type: str, metadata: dict) -> StoredObject: ...
    async def get(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def presign_get(self, key: str, ttl_s: int = 300) -> str: ...
    async def health_check(self) -> HealthResult: ...
```

| Adapter | Use |
|---|---|
| `lygo_managed` | RumptyCloud Object Storage (S3-compatible) — default |
| `s3_compatible` | AWS S3, MinIO, Wasabi, any S3 API in a Kenyan data centre |
| `azure_blob` | Firms on Microsoft 365 / Azure |
| `gcs` | Google Cloud Storage |
| `sftp` | On-premise servers |
| `webhook_push` | POST artefacts/manifests to a firm endpoint (signed, retried) |
| `vector_pg` (Enterprise) | Firm-hosted Postgres + pgvector |

## Security for destinations

- Credentials stored encrypted (envelope encryption: per-firm data key wrapped by a master key
  in KMS/Vault), never returned by the API after creation.
- Prefer scoped credentials (bucket-only IAM, SAS tokens with expiry).
- On setup, Lygo runs a write/read/delete probe and shows the result in the Control Center.
- Health checked periodically; failures alert the firm owner and queue writes for retry.
- Every write/read through an adapter is in the audit log.

## Residency

Each destination records its **country/region**. Because hosting outside Kenya counts as a
cross-border transfer under the Data Protection Act and the ODPC's 2026 guidance, the Control
Center shows the region and requires the firm to confirm the legal basis (see
[07](07-security-and-compliance.md)). Firms that want data kept in Kenya can choose a
Kenyan-hosted BYO bucket.

## Deletion & export

- Firm offboarding: full export (originals + metadata JSON + reports) to a destination of choice,
  then crypto-shred the firm key and delete; deletion certificate generated.
- Client deletion: removes client documents, chunks, memory, chat; keeps audit entries
  (minimised) for the legal retention period.
