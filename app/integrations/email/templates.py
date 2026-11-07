"""Email templates. Email is not a secure channel either, so (like SMS, docs/10) messages carry only a link/code, the firm name
and generic wording: never case details, document names, client names or amounts. Parameters are validated."""
import html
import re
from dataclasses import dataclass
from urllib.parse import quote

from app.core.config import settings
from app.integrations.email.base import EmailMessage

_FIRM = r"[A-Za-z0-9 &.,'\\-]{1,60}"


class EmailTemplateError(ValueError): ...


@dataclass(frozen=True)
class EmailTemplate:
    id: str
    subject: str
    text: str
    params: dict[str, str]  # name -> full-match regex


TEMPLATES = {
    t.id: t
    for t in (
        EmailTemplate(
            "invitation", "You've been invited to {firm} on Lygo",
            "{firm} has invited you to join them on Lygo.\n\nAccept the invitation (valid for 7 days):\n{link}\n\n"
            "If you weren't expecting this, you can ignore this email. No account is created until you accept.",
            {"firm": _FIRM, "link": r"https?://\S+"},
        ),
        EmailTemplate(
            "firm_approved", "Your firm {firm} is ready on Lygo",
            "Good news: your application for {firm} has been approved and you are its owner.\n\nSign in to set up your workspace:\n{link}\n\n"
            "Firm staff must use two-factor authentication; you will be guided through it after signing in.",
            {"firm": _FIRM, "link": r"https?://\S+"},
        ),
        EmailTemplate(
            "firm_rejected", "About your Lygo application for {firm}",
            "We could not approve your application for {firm}. Sign in to see the reason and, if appropriate, apply again:\n{link}",
            {"firm": _FIRM, "link": r"https?://\S+"},
        ),
        EmailTemplate(
            "password_reset", "Reset your Lygo password",
            "We received a request to reset your Lygo password.\n\nChoose a new password (the link works once and expires in {minutes} minutes):\n{link}\n\n"
            "If you didn't ask for this, ignore this email; your password stays as it is.",
            {"minutes": r"\d{1,3}", "link": r"https?://\S+"},
        ),
        EmailTemplate(
            "security_alert", "Security alert for your Lygo account",
            "{event} on {when} (UTC).\n\nIf this was you, no action is needed. If it wasn't, reset your password now and contact your firm administrator:\n{link}",
            {"event": r"(?:Your password was changed|Two-factor authentication was turned on|Two-factor authentication was turned off|"
                      r"A new phone number was verified|Your recovery codes were regenerated|All your sessions were signed out)",
             "when": r"[0-9T:\- ]{10,25}", "link": r"https?://\S+"},
        ),
    )
}


def link(path: str, **query: str) -> str:
    """Build a frontend URL; the base comes from configuration only, never from request data."""
    qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in query.items())
    return f"{settings.frontend_base_url.rstrip('/')}/{path.lstrip('/')}" + (f"?{qs}" if qs else "")


def render(template_id: str, to: str, params: dict[str, str]) -> EmailMessage:
    t = TEMPLATES.get(template_id)
    if t is None:
        raise EmailTemplateError(f"unknown email template {template_id!r}")
    if set(params) != set(t.params):
        raise EmailTemplateError(f"template {template_id!r} requires exactly {sorted(t.params)}")
    for name, pattern in t.params.items():
        if not re.fullmatch(pattern, params[name]):
            raise EmailTemplateError(f"parameter {name!r} is not allowed in an email")
    subject = t.subject.format(**params)
    text = t.text.format(**params)
    safe = html.escape(text).replace("\n", "<br>")  # every value is escaped before it can reach the HTML part
    if "link" in params:
        url = html.escape(params["link"], quote=True)
        safe = safe.replace(html.escape(params["link"]), f'<a href="{url}">{url}</a>')
    body_html = f"<html><body><p>{safe}</p></body></html>"
    return EmailMessage(to=to, subject=subject, body=text, html=body_html, tags={"template": template_id})
