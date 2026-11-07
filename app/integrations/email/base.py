"""Email sender interface. Real SMTP/provider adapter arrives with Phase F (notifications)."""
from dataclasses import dataclass, field
from typing import Protocol

import structlog


@dataclass
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    """Local default: logs a masked line; never prints message bodies (they may hold tokens)."""

    async def send(self, message: EmailMessage) -> None:
        structlog.get_logger().info("email.queued", to=message.to, subject=message.subject)


@dataclass
class InMemoryEmailSender:
    """For tests."""

    sent: list[EmailMessage] = field(default_factory=list)

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


_default: EmailSender = ConsoleEmailSender()


def get_email_sender() -> EmailSender:
    """FastAPI dependency; override in tests."""
    return _default
