"""Structured logging with PII masking. See docs/07-security-and-compliance.md."""
import logging
import re
from collections.abc import MutableMapping
from typing import Any

import structlog

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?254|0)[17]\d{8}(?!\d)"), "[phone]"),  # Kenyan mobile numbers
    (re.compile(r"\b[AP]\d{9}[A-Z]\b"), "[kra-pin]"),
    (re.compile(r"(?<!\d)\d{7,8}(?!\d)"), "[id-number]"),  # national ID / passport-like
]
_SENSITIVE_KEYS = {"password", "token", "authorization", "api_key", "secret", "prompt"}
_UNMASKED_KEYS = {"request_id", "event", "job", "notification_id", "transaction_id"}  # identifiers, not personal data


def mask_pii(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {k: "[redacted]" if k.lower() in _SENSITIVE_KEYS else _mask_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_value(v) for v in value]
    return value


def pii_processor(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> dict[str, Any]:
    return {
        k: "[redacted]" if k.lower() in _SENSITIVE_KEYS else (v if k in _UNMASKED_KEYS else _mask_value(v)) for k, v in event_dict.items()
    }


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            pii_processor,
            structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )
