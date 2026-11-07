"""SMS provider interface. See docs/10-notifications-sms.md."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Protocol


@dataclass
class SmsResult:
    provider_message_id: str
    status: str  # queued | sent | failed


class SmsProvider(Protocol):
    async def send(self, to: str, text: str, *, sender_id: str, idempotency_key: str) -> SmsResult: ...
    async def balance(self) -> Decimal: ...
    def verify_dlr_signature(self, headers: Mapping[str, str], body: bytes) -> bool: ...
