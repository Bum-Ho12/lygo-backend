"""Thrivewave SMS adapter — placeholder.

Endpoints, auth and DLR format are not yet confirmed (no public docs found).
Fill in from Thrivewave's developer documentation and record details in THRIVEWAVE.md.
"""
from collections.abc import Mapping
from decimal import Decimal

from app.integrations.sms.base import SmsResult


class ThrivewaveProvider:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self.api_key = api_key

    async def send(self, to: str, text: str, *, sender_id: str, idempotency_key: str) -> SmsResult:
        raise NotImplementedError("Confirm Thrivewave send endpoint and payload")

    async def balance(self) -> Decimal:
        raise NotImplementedError("Confirm Thrivewave balance endpoint")

    def verify_dlr_signature(self, headers: Mapping[str, str], body: bytes) -> bool:
        raise NotImplementedError("Confirm Thrivewave delivery-report signing")
