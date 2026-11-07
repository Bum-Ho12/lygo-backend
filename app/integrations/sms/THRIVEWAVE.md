# Thrivewave (ThriveVas) integration notes

Source: https://docs.thrivevas.com/ (single-page app; sections are served as Markdown at `/content/<id>.md`).
Verified against the live API on 2026-11-07 using a non-sending request (see "How the key was checked").

| Item | Value |
|---|---|
| Base URL (prod / sandbox) | `https://sms.thrivevas.com` / **no sandbox documented** |
| Auth method | header `x-api-key: <token>`. The token is generated at `https://identity.thrivevas.com/v1/auth/api-key/generate` with Basic auth (account MSISDN + password) and `{"name","lifetime_seconds"}` (docs example: 86400 s) — **tokens can expire** |
| Send endpoint | `POST /v1/message/send` JSON `{message, msisdn, sender_id?, callback_url?}` (+ `schedule`, `date`, `send_time`, `country_code`) |
| Send bulk | same endpoint with up to **50 comma-separated** numbers; or `POST /v1/message/send/upload-file` (CSV, < 100 MB, `[column]` placeholders) |
| Success response | `{"message":"SMS successfully queued for sending. Total recipients N","status":200,"transaction_id":2295}` — one `transaction_id` per request |
| Error response | `{"status":<code>,"message":"...","error_code":"","transaction_id":0}`; codes: 400/401/403/404/405/406/418/421/422/428/429/500/503 |
| Rate limit | 429 exists; numeric limits not documented |
| Delivery report | POST to the `callback_url` given per request: `{msisdn, status, status_description, country, network, transaction_id}`; status 0/1 failed, 2 pending, 3 delivered |
| DLR signing / verification | **None documented** |
| Idempotency | **None documented** |
| Balance endpoint | **None documented** |
| Sender ID registration | not documented; default sender `ThriveVas` |
| Supported networks | DLR shows `network` (e.g. Safaricom); list not documented |
| Pricing per segment | not documented |
| Data processing agreement signed? | **No — required before production use** (sub-processor) |
| Data location / retention of message logs | not documented |

## Design consequences
- **Unsigned callbacks:** each notification gets its own callback URL containing a random secret token
  (`/v1/integrations/thrivewave/delivery-reports/{token}`); only its SHA-256 is stored. A report is accepted only if the token
  matches, the `transaction_id` equals the one returned at send time, and the number matches. Anyone who learns a token can
  forge that one message's status, which is the best available without provider-side signing; ask Thrivewave for signing.
- **No idempotency key:** we send one recipient per request, claim the notification row (`queued -> sending`) before the HTTP
  call, retry only on definite non-delivery (connection refused, 429, 503), and mark timeouts/5xx `uncertain` instead of
  retrying.
- **No balance API:** quota/metering is tracked on our side per firm.
- **Token expiry:** 401/428 raises an auth error and the notification is marked failed with a clear reason; rotate the key.

## How the key was checked
A request with an empty message and a non-numeric recipient was sent: the supplied key got HTTP 422 `missing message`
(auth passed), a deliberately wrong key got HTTP 401 `authorization failed`. No SMS was sent.

## Open questions for Thrivewave
Callback signing, idempotency/dedupe key, sandbox, rate limits, sender-ID registration with the Communications Authority,
DPA / sub-processor terms and message-log retention, token lifetime for long-running integrations.
