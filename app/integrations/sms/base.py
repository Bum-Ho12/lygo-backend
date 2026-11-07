"""SMS provider interface. See docs/10-notifications-sms.md and app/integrations/sms/THRIVEWAVE.md."""
from dataclasses import dataclass
from typing import Protocol


class SmsError(Exception): ...


class SmsAuthError(SmsError):
    """API key rejected or expired (HTTP 401 / provider 428). Needs operator action; do not retry."""


class SmsRejected(SmsError):
    """The provider refused the request (invalid parameters). Retrying will not help."""


class SmsTransientError(SmsError):
    """Definitely not accepted (429/503/connection refused). Safe to retry."""


class SmsUncertain(SmsError):
    """The request may have reached the provider (timeout / 5xx). NEVER retry blindly: the API has no
    idempotency key, so a retry could deliver the message twice."""


@dataclass(frozen=True)
class SmsResult:
    provider_message_id: str  # ThriveVas `transaction_id` (one per request)
    status: str  # queued
    recipients: int


@dataclass(frozen=True)
class DlrEvent:
    msisdn: str
    transaction_id: str
    status: str  # delivered | failed | pending
    detail: str = ""
    network: str = ""


class SmsProvider(Protocol):
    async def send(self, to: str, text: str, *, sender_id: str | None = None, callback_url: str | None = None) -> SmsResult: ...
