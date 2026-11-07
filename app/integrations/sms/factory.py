from functools import lru_cache

import structlog

from app.core.config import settings
from app.integrations.sms.base import SmsProvider, SmsResult
from app.integrations.sms.phone import mask
from app.integrations.sms.thrivewave import ThrivewaveProvider


class ConsoleSmsProvider:
    """LOCAL DEVELOPMENT ONLY: logs the message (including OTP codes) instead of sending it. Never returned outside local env."""

    async def send(self, to: str, text: str, *, sender_id: str | None = None, callback_url: str | None = None) -> SmsResult:
        structlog.get_logger().warning("sms.console", to=mask(to), text=text)
        return SmsResult(provider_message_id="console", status="queued", recipients=1)


@lru_cache
def _thrivewave() -> ThrivewaveProvider:
    return ThrivewaveProvider(settings.thrivewave_base_url, settings.thrivewave_api_key)  # one pooled client for the process


def get_sms_provider() -> SmsProvider | None:
    """FastAPI dependency; override in tests. None => SMS unavailable (callers return 503 / fall back to other methods)."""
    if settings.thrivewave_base_url and settings.thrivewave_api_key:
        return _thrivewave()
    return ConsoleSmsProvider() if settings.is_local else None
