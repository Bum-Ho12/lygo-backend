"""SMS templates. Only registered templates can be sent: SMS is not a secure channel (docs/10), so free text
and case details can never reach it. Parameters are validated against strict patterns."""
import math
import re
import unicodedata
from dataclasses import dataclass

from app.core.config import settings

_GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM7_EXT = set("^{}\\[~]|€")  # cost two septets each


class TemplateError(ValueError): ...


@dataclass(frozen=True)
class Template:
    id: str
    version: int
    body: str
    params: dict[str, str]  # name -> regex the value must fully match
    urgent: bool = False  # urgent messages ignore quiet hours (e.g. OTP)


TEMPLATES: dict[str, Template] = {
    t.id: t
    for t in (
        Template("otp", 1, "Your Lygo code is {code}. It expires in {minutes} minutes. Do not share it.",
                 {"code": r"\d{4,8}", "minutes": r"\d{1,2}"}, urgent=True),
        Template("update_nudge", 1, "Lygo: {firm} has an update for you. Log in to view it.", {"firm": r"[A-Za-z0-9 &.,'\-]{1,30}"}),
        Template("document_reviewed", 1, "Lygo: {firm} has reviewed a document you shared. Log in to view it.", {"firm": r"[A-Za-z0-9 &.,'\-]{1,30}"}),
        Template("deadline_reminder", 1, "Lygo: {firm} reminder - you have a deadline coming up. Log in for details.", {"firm": r"[A-Za-z0-9 &.,'\-]{1,30}"}),
    )
}


def fold_gsm7(text: str) -> str:
    """Replace characters outside GSM-7 with ASCII equivalents (or '?') to avoid costly UCS-2 messages."""
    out = []
    for ch in text:
        if ch in _GSM7 or ch in _GSM7_EXT:
            out.append(ch)
            continue
        base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
        out.append(base if base and base in _GSM7 else "?")
    return "".join(out)


def septets(text: str) -> int:
    return sum(2 if ch in _GSM7_EXT else 1 for ch in text)


def segments(text: str) -> int:
    n = septets(text)
    return 1 if n <= 160 else math.ceil(n / 153)


def render(template_id: str, params: dict[str, str]) -> tuple[Template, str]:
    t = TEMPLATES.get(template_id)
    if t is None:
        raise TemplateError(f"unknown SMS template {template_id!r}")
    if set(params) != set(t.params):
        raise TemplateError(f"template {template_id!r} requires exactly {sorted(t.params)}")
    clean = {}
    for name, pattern in t.params.items():
        value = fold_gsm7(str(params[name]).strip())
        if not re.fullmatch(pattern, value):
            raise TemplateError(f"parameter {name!r} is not allowed in an SMS")
        clean[name] = value
    text = t.body.format(**clean)
    if segments(text) > settings.sms_max_segments:
        raise TemplateError("rendered message is too long")
    return t, text
