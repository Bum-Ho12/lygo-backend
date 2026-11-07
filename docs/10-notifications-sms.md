# 10 — Notifications & SMS (Thrivewave)

## Channels

| Channel | Used for |
|---|---|
| In-app (SSE/websocket) | Processing updates, new flags, chat replies, approvals |
| Email | Invitations, reports ready, weekly digests |
| SMS (Thrivewave) | OTP / login verification, "you have a new update" nudges, deadline reminders, appointment reminders |

## Integration design

`app/integrations/sms/` defines a provider interface so Thrivewave can be swapped or a fallback
added without touching business code:

```python
class SmsProvider(Protocol):
    async def send(self, to: str, text: str, *, sender_id: str, idempotency_key: str) -> SmsResult: ...
    async def balance(self) -> Decimal: ...
    def verify_dlr_signature(self, headers: Mapping[str, str], body: bytes) -> bool: ...
```

`thrivewave.py` implements it. Requirements for the adapter:

- Phone numbers normalised to E.164 (`+2547XXXXXXXX`, `+2541XXXXXXXX`).
- Sent via a Celery queue (`sms`) with retries and an idempotency key so retries never double-send.
- Delivery reports received at `POST /v1/integrations/thrivewave/delivery-reports` and stored on
  `notifications.status`.
- Per-firm SMS quotas and usage metering (SMS cost is passed through on billing).
- Optional firm-specific sender ID (firm's registered alphanumeric sender ID) or Lygo default.

> **Action needed:** we could not find public Thrivewave API documentation in our research.
> Before implementing, get from Thrivewave: base URL, auth method, send/bulk endpoints,
> DLR webhook format and signing, sender ID registration process, rate limits, sandbox access,
> and their data-processing terms (they become a sub-processor). Record answers in
> `app/integrations/sms/THRIVEWAVE.md`.

## Content rules (privacy)

SMS is not a secure channel. Messages must **never** contain case details, document contents,
names of opposing parties, health or other sensitive data.

Good: `Lygo: Your advocate at <Firm> has an update for you. Log in at <short link>.`
Bad: `Your work permit application was rejected because...`

OTP: `Your Lygo code is 482913. It expires in 5 minutes. Do not share it.`

## Compliance

- Use a sender ID registered through the provider with the Communications Authority of Kenya.
- Transactional messages only by default; any promotional SMS needs prior consent and an opt-out
  (e.g. STOP keyword) honoured immediately.
- Respect user notification preferences and quiet hours (no non-urgent SMS 21:00–07:00 EAT).
- Thrivewave must be on the sub-processor list with a DPA.

## Templates

Stored in `app/integrations/sms/templates/` with an ID, version and max length (160 GSM-7 chars
per segment). English first; Swahili variants in Phase 2.
