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
    _warn_console_once()
    return ConsoleSmsProvider() if settings.is_local else None


@lru_cache
def _warn_console_once() -> None:
    structlog.get_logger().warning(
        "sms.not_configured",
        detail="THRIVEWAVE_BASE_URL / THRIVEWAVE_API_KEY not set: SMS will NOT be sent (local dev logs them instead). Check that .env is loaded.",
    )


def describe() -> str:
    """Which SMS path is active, for the startup log."""
    if settings.thrivewave_base_url and settings.thrivewave_api_key:
        return f"thrivewave ({settings.thrivewave_base_url}, sender={settings.thrivewave_sender_id or 'provider default'})"
    return "console (local only, nothing is sent)" if settings.is_local else "none (SMS disabled)"
