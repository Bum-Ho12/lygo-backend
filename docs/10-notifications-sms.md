# 10 — Notifications & SMS (Thrivewave)

## Channels

| Channel | Used for |
|---|---|
| In-app (SSE/websocket) | Processing updates, new flags, chat replies, approvals |
| Email | Invitations, reports ready, weekly digests |
| SMS (Thrivewave) | OTP / login verification, "you have a new update" nudges, deadline reminders, appointment reminders |

## Integration design

`app/integrations/sms/` defines a provider interface so Thrivewave (ThriveVas) can be swapped or a fallback added
without touching business code. Provider facts, verified against https://docs.thrivevas.com/ and the live API, are in
[`THRIVEWAVE.md`](../app/integrations/sms/THRIVEWAVE.md).

```python
class SmsProvider(Protocol):
    async def send(self, to: str, text: str, *, sender_id: str | None = None, callback_url: str | None = None) -> SmsResult: ...
```

What the provider's API shapes about our design (it differs from the original placeholder):

- **No idempotency key.** One recipient per request; the notification row is *claimed* (`queued -> sending`) atomically before
  the HTTP call; only definite non-delivery (connection refused, 429, 503) is retried (backoff 1 s, 2 s, max 3 attempts);
  timeouts/5xx end as `uncertain` and are **never** auto-retried (a retry could double-send).
- **Unsigned delivery reports.** Each message's `callback_url` carries a derived per-message secret
  (`/v1/integrations/thrivewave/delivery-reports/{token}`, only its hash is stored). A report is applied only if the token,
  the `transaction_id` and the recipient number all match, only moves non-terminal messages, and unknown tokens get a 404.
  This is weaker than signing; ask Thrivewave for signed callbacks.
- **No balance API**: quotas are metered on our side (`SMS_MONTHLY_QUOTA` per firm; excess returns 429).
- **API key may expire**: 401/428 fails the message fast with a clear error and an error log to rotate the key.
- Statuses: `queued -> sending -> sent -> delivered | failed`, plus `uncertain`. Once terminal, the recipient number and message text
  are erased (data minimisation); the masked number is kept for the audit view (`GET /firms/{id}/notifications`, admins only).
- Phone numbers are normalised to E.164 (`+2547XXXXXXXX`, `+2541XXXXXXXX`); only Kenyan mobile numbers are accepted for now.
- Delivery runs inline or on the `sms` Celery queue; messages held by quiet hours are released by `send_due` (run it from
  Celery beat, not yet scheduled).

## OTP (login and phone verification)
Implemented in `app/domain/users/otp.py`: 6-digit codes from a CSPRNG, stored only as a keyed hash, 5-minute TTL, 5 attempts per code, single
use, a new code invalidates older ones. Codes go **only** to a number the user has verified (or is verifying), never to a number supplied
at login. Sends are limited to 3 per user per 10 minutes and 20 per IP per hour (SMS-pumping and cost protection). SMS is a fallback
factor, not the primary one for staff: see [ADR 0007](adr/0007-authentication-factors-and-email.md).

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
