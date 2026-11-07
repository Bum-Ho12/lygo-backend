import asyncio
import email
import email.policy

import pytest
from aiosmtpd.controller import Controller

from app.core.config import settings
from app.integrations.email.base import (
    ConsoleEmailSender,
    EmailError,
    EmailMessage,
    UnavailableEmailSender,
    get_email_sender,
)
from app.integrations.email.smtp import SmtpEmailSender, build_mime
from app.integrations.email.templates import EmailTemplateError, link, render

pytestmark = pytest.mark.asyncio


class Inbox:
    def __init__(self) -> None:
        self.messages: list[email.message.EmailMessage] = []
        self.envelopes: list[tuple[str, list[str]]] = []
        self.reject: str | None = None

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # type: ignore[no-untyped-def]
        if self.reject and self.reject in address:
            return "550 5.1.1 No such user"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):  # type: ignore[no-untyped-def]
        self.messages.append(email.message_from_bytes(envelope.content, policy=email.policy.default))  # type: ignore[arg-type]
        self.envelopes.append((envelope.mail_from, list(envelope.rcpt_tos)))
        return "250 Message accepted"


@pytest.fixture
def smtp():  # type: ignore[no-untyped-def]
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    inbox = Inbox()
    ctl = Controller(inbox, hostname="127.0.0.1", port=port)
    ctl.start()
    yield inbox, port
    ctl.stop()


def sender(port: int, **kw) -> SmtpEmailSender:  # type: ignore[no-untyped-def]
    return SmtpEmailSender("127.0.0.1", port, security="none", from_addr="no-reply@lygo.example", from_name="Lygo", timeout=5, **kw)


# --- the real SMTP wire protocol --------------------------------------------------------------------------------------------------
async def test_smtp_delivers_multipart_with_safe_headers(smtp) -> None:  # type: ignore[no-untyped-def]
    inbox, port = smtp
    await sender(port).send(render("invitation", "ann@example.com", {"firm": "Muthoni & Co.", "link": "https://app.example/accept-invite?token=abc"}))
    (m,) = inbox.messages
    assert m["To"] == "ann@example.com" and m["Subject"] == "You've been invited to Muthoni & Co. on Lygo"
    assert "Lygo" in m["From"] and "no-reply@lygo.example" in m["From"] and m["Auto-Submitted"] == "auto-generated"
    assert m["Message-ID"].endswith("@lygo.example>") and m["Date"]
    text, html = m.get_body(("plain",)).get_content(), m.get_body(("html",)).get_content()
    assert "token=abc" in text and 'href="https://app.example/accept-invite?token=abc"' in html
    assert inbox.envelopes == [("no-reply@lygo.example", ["ann@example.com"])]


async def test_smtp_error_classification(smtp) -> None:  # type: ignore[no-untyped-def]
    inbox, port = smtp
    inbox.reject = "bounce"
    with pytest.raises(EmailError) as perm:
        await sender(port).send(EmailMessage("bounce@example.com", "s", "b"))
    assert perm.value.transient is False  # 550: the address is wrong, do not retry
    with pytest.raises(EmailError) as down:
        await sender(1).send(EmailMessage("a@example.com", "s", "b"))  # nothing listening
    assert down.value.transient is True


async def test_header_injection_and_bad_addresses_are_rejected(smtp) -> None:  # type: ignore[no-untyped-def]
    inbox, port = smtp
    for bad in ("a@example.com\r\nBcc: evil@example.com", "not-an-address", "a@b", "a b@example.com", "<a@example.com>"):
        with pytest.raises(EmailError):
            await sender(port).send(EmailMessage(bad, "s", "b"))
    with pytest.raises(EmailError):
        await sender(port).send(EmailMessage("a@example.com", "Hi\r\nBcc: evil@example.com", "b"))
    assert inbox.messages == []
    assert build_mime(EmailMessage("a@example.com", "ok", "b"), "x@lygo.example", "Lygo")["Bcc"] is None


def test_invalid_security_mode_is_rejected() -> None:
    with pytest.raises(EmailError):
        SmtpEmailSender("h", 25, security="plaintext-please", from_addr="a@b.co")


async def test_default_sender_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "")
    assert isinstance(get_email_sender(), ConsoleEmailSender)  # local dev: log only
    monkeypatch.setattr(settings, "app_env", "staging")
    s = get_email_sender()
    assert isinstance(s, UnavailableEmailSender)  # elsewhere: fail loudly rather than silently drop invitations
    with pytest.raises(EmailError) as e:
        await s.send(EmailMessage("a@example.com", "s", "b"))
    assert e.value.transient is False
    monkeypatch.setattr(settings, "smtp_host", "smtp.provider.example")
    assert isinstance(get_email_sender(), SmtpEmailSender)
    assert asyncio  # keep import used for fixtures


# --- templates (privacy policy) ------------------------------------------------------------------------------------------------------
def test_templates_render_and_escape() -> None:
    m = render("password_reset", "a@example.com", {"minutes": "30", "link": "https://app.example/reset?token=x&y=1"})
    assert "30 minutes" in m.body and m.tags == {"template": "password_reset"}
    assert "token=x&amp;y=1" in m.html and "<script" not in m.html  # & is escaped inside the HTML part
    alert = render("security_alert", "a@example.com", {"event": "Your password was changed", "when": "2026-11-07 12:00", "link": "https://app.example/x"})
    assert alert.body.startswith("Your password was changed on 2026-11-07 12:00 (UTC).")


@pytest.mark.parametrize(
    ("tid", "params"),
    [
        ("nope", {}),
        ("invitation", {"firm": "Acme"}),  # missing link
        ("invitation", {"firm": "Acme", "link": "https://x.example", "extra": "1"}),
        ("invitation", {"firm": "<script>alert(1)</script>", "link": "https://x.example"}),
        ("invitation", {"firm": "Firm\nBcc: x@y.z", "link": "https://x.example"}),
        ("invitation", {"firm": "Acme", "link": "javascript:alert(1)"}),
        ("security_alert", {"event": "Your client John Doe lost their case", "when": "2026-11-07 12:00", "link": "https://x.example"}),  # no free text
        ("password_reset", {"minutes": "soon", "link": "https://x.example"}),
    ],
)
def test_templates_reject_free_text_and_injection(tid: str, params: dict[str, str]) -> None:
    with pytest.raises(EmailTemplateError):
        render(tid, "a@example.com", params)


def test_links_come_from_configuration_and_are_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "frontend_base_url", "https://app.lygo.example/")
    assert link("accept-invite", token="a b&c") == "https://app.lygo.example/accept-invite?token=a%20b%26c"
