"""SMTP adapter (aiosmtplib). Works with any provider's SMTP relay (SES, Postmark, Mailgun, Brevo...) or Mailpit locally."""
import email.utils
import re
import ssl
from email.message import EmailMessage as MimeMessage

import aiosmtplib

from app.core.config import settings
from app.integrations.email.base import EmailError, EmailMessage

_HEADER_UNSAFE = re.compile(r"[\r\n\x00]")
_ADDRESS = re.compile(r"^[^@\s<>,;\"']+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _clean_header(value: str) -> str:
    if _HEADER_UNSAFE.search(value):
        raise EmailError("header injection attempt rejected", transient=False)
    return value.strip()


def build_mime(msg: EmailMessage, from_addr: str, from_name: str) -> MimeMessage:
    to = _clean_header(msg.to)
    if not _ADDRESS.match(to):
        raise EmailError(f"invalid recipient address: {to!r}", transient=False)
    m = MimeMessage()
    m["From"] = email.utils.formataddr((_clean_header(from_name), _clean_header(from_addr)))
    m["To"] = to
    m["Subject"] = _clean_header(msg.subject)
    m["Date"] = email.utils.formatdate(localtime=False)
    m["Message-ID"] = email.utils.make_msgid(domain=from_addr.split("@", 1)[-1])
    m["Auto-Submitted"] = "auto-generated"  # suppress auto-replies / vacation responders
    m["X-Auto-Response-Suppress"] = "All"
    m.set_content(msg.body)
    if msg.html:
        m.add_alternative(msg.html, subtype="html")
    return m


class SmtpEmailSender:
    delivers = True

    def __init__(
        self, host: str, port: int, *, username: str = "", password: str = "", security: str = "starttls", from_addr: str, from_name: str = "Lygo",
        timeout: float = 15.0, tls_context: ssl.SSLContext | None = None,
    ) -> None:
        if security not in {"starttls", "tls", "none"}:
            raise EmailError(f"invalid SMTP_SECURITY {security!r}", transient=False)
        self.host, self.port, self.username, self.password = host, port, username, password
        self.security, self.from_addr, self.from_name, self.timeout, self.tls_context = security, from_addr, from_name, timeout, tls_context

    @classmethod
    def from_settings(cls) -> "SmtpEmailSender":
        return cls(settings.smtp_host, settings.smtp_port, username=settings.smtp_username, password=settings.smtp_password,
                   security=settings.smtp_security, from_addr=settings.email_from, from_name=settings.email_from_name)

    async def send(self, message: EmailMessage) -> None:
        mime = build_mime(message, self.from_addr, self.from_name)
        try:
            await aiosmtplib.send(
                mime, hostname=self.host, port=self.port, username=self.username or None, password=self.password or None,
                use_tls=self.security == "tls", start_tls=self.security == "starttls", timeout=self.timeout, tls_context=self.tls_context,
            )
        except aiosmtplib.SMTPRecipientsRefused as exc:
            raise EmailError(f"recipient refused: {exc}", transient=False) from exc
        except aiosmtplib.SMTPAuthenticationError as exc:
            raise EmailError("SMTP authentication failed (check SMTP_USERNAME / SMTP_PASSWORD)", transient=False) from exc
        except aiosmtplib.SMTPResponseException as exc:
            raise EmailError(f"SMTP error {exc.code}", transient=400 <= exc.code < 500) from exc
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
            raise EmailError(f"could not deliver: {type(exc).__name__}", transient=True) from exc
