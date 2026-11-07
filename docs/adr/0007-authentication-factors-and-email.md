# ADR 0007 — Authentication factors: SMS OTP, TOTP and why email is still required

- Status: Accepted
- Date: 2026-11-07

## Question
Lygo verifies users with SMS one-time codes (OTP). Do we still need an email channel (SMTP)?

## Decision
**Yes. SMS OTP and email do different jobs and neither replaces the other.** We run both, and we add an authenticator app
(TOTP) as the preferred second factor for staff.

| Need | Channel | Why not the other |
|---|---|---|
| **Delivering invitations** (firm owners, members, client-portal users) | Email | An invitation is addressed to an *email* and carries a token. Today `ConsoleEmailSender` only logs, so without SMTP **nobody outside local dev can join a firm**. OTP cannot help: the invitee has no account or verified phone yet. |
| **Account recovery / password reset** | Email | Recovery needs a channel that is independent of the thing being recovered and that the account owner controls. SMS-only recovery makes SIM swap a full account takeover. |
| **Security alerts** (password changed, MFA turned off, new phone) | Email (+ SMS optional) | An alert is only useful on a channel the attacker does not already control. After a SIM swap the attacker receives the SMS; the legitimate owner's mailbox is unaffected. |
| **Controller notifications** (breach notice, DPA s.43 / 72-hour ODPC timeline, compliance mail to firm owners) | Email | Durable, auditable, works for non-Kenyan and non-mobile recipients. |
| **Reports ready, digests, longer messages** | Email | SMS is limited to 160 chars, costs money per segment and must never carry case detail (docs/10). |
| **Phone ownership proof / second factor / step-up** | SMS OTP | This is what OTP is good for: proving possession of a phone number, cheaply, to people without an authenticator app. |
| **Strong second factor for staff** | TOTP (+ recovery codes) | Not interceptable by SIM swap or SMS gateway compromise; works offline; free. |

### Why SMS must not be the only factor
- **SIM swap is the dominant account-takeover route in Kenya.** INTERPOL's *African Cyberthreat Assessment 2026* reports a 327% rise in Kenyan SIM-swap fraud in 2025 (≈123,000 fraudulent SIMs, ≈US$3.8m drained); in June 2026 the High Court held a telco and a bank jointly liable for a swap loss. An attacker who swaps the SIM receives every SMS code.
- **NIST SP 800-63B (Rev 4)** is reported to classify SMS/PSTN out-of-band authentication as a *restricted* authenticator that verifiers must treat with extra risk checks and offer an alternative to. (We read this from secondary sources; confirm sections 3.1.3.3 / 3.2.9 against the primary text before citing it externally.)
- SMS OTP is **not phishing-resistant** (a fake login page can relay the code).
- **SMS pumping** (fraudsters trigger sends to premium numbers) is a direct cost attack. Our sends go only to a number the user has already verified, and are rate limited per user and per IP.
- Phone numbers are recycled by operators; an old number can reach a stranger.

### Resulting policy
1. **Staff (all firm roles, platform staff): a second factor is mandatory** (`MFA_REQUIRED_FOR_FIRM_ROLES`, enforced in the access-token claim and refused outright in production config). TOTP is the recommended factor; SMS is allowed as a fallback.
2. **Client-portal users: second factor optional** (SMS or TOTP), so that low-tech clients are not locked out.
3. **A password reset never satisfies MFA.** Reset links are single use, expire in 30 minutes, end all sessions, and the next login still demands the second factor. Compromising a mailbox alone must not yield account access.
4. **Sensitive changes need re-authentication** (password, plus a current code when an authenticator is on): disabling TOTP, regenerating recovery codes, toggling the SMS factor.
5. **Every security-relevant change emails the owner** (best effort; failure never blocks the action).
6. **Rate limiting everywhere a guess is possible**: login (per account+IP, per account, per IP), second-factor attempts, OTP sends, OTP verification, password-reset requests.

### What "email" means operationally
We do **not** need to run a mail server. We need an outbound channel: SMTP credentials for a transactional provider (any SMTP relay) or Mailpit locally. The `EmailSender` interface supports an HTTP-API adapter later. Requirements:
- A sending **domain we control** with SPF, DKIM and DMARC (open question #10: product domain) or invitations will land in spam.
- The provider is a **sub-processor**: DPA, Kenyan Standard Clauses if outside Kenya, listed in the sub-processor list (docs/07).
- Email, like SMS, **never carries case detail**: only a link or code, the firm name and fixed wording (enforced by the template registry).
- Outside local env with no `SMTP_HOST`, sending **fails loudly** instead of silently dropping invitations; invitation APIs report `email_sent=false` and offer a re-send.

## Consequences
- More moving parts than SMS-only, but each failure mode is covered by a second channel.
- Email addresses remain the account identifier; they are marked verified when the owner proves control (accepting an invitation or redeeming a reset link).
- Registration still reveals whether an email exists (`409 email_taken`). This is a conscious trade-off for usability; the sensitive flows (login, forgot-password) do not leak existence.
- Not built yet: WebAuthn/passkeys (phishing-resistant, the recommended long-term factor), SSO (OIDC) for firms, "remember this device", SIM-swap signals from the operator, and column-level grants to stop the app role reading all of `users`.
