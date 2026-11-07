"""Email sender interface. Adapters: SMTP (any provider relay / Mailpit), console (local), in-memory (tests)."""
from dataclasses import dataclass, field
from typing import Protocol

import structlog

from app.core.config import settings


class EmailError(Exception):
    """Delivery failed. `transient` => safe to retry later; otherwise the address/config is wrong."""

    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass
class EmailMessage:
    to: str
    subject: str
    body: str  # plain text (always present)
    html: str | None = None
    tags: dict[str, str] = field(default_factory=dict)  # e.g. {"template": "invitation"} for logs/metrics; never PII


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    """Local default: logs a masked line; never prints message bodies (they contain tokens)."""

    async def send(self, message: EmailMessage) -> None:
        structlog.get_logger().info("email.queued", to=message.to, subject=message.subject, **message.tags)


class UnavailableEmailSender:
    """Outside local with no SMTP configured: fail loudly instead of silently dropping invitations."""

    async def send(self, message: EmailMessage) -> None:
        raise EmailError("outbound email is not configured (set SMTP_HOST)", transient=False)


@dataclass
class InMemoryEmailSender:
    """For tests."""

    sent: list[EmailMessage] = field(default_factory=list)

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


def get_email_sender() -> EmailSender:
    """FastAPI dependency; override in tests."""
    if settings.smtp_host:
        from app.integrations.email.smtp import SmtpEmailSender

        return SmtpEmailSender.from_settings()
    return ConsoleEmailSender() if settings.is_local else UnavailableEmailSender()
