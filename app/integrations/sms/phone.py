"""Kenyan mobile number normalisation to E.164 (+2547XXXXXXXX / +2541XXXXXXXX)."""
import re

_DIGITS = re.compile(r"\D")


class InvalidPhone(ValueError): ...


def normalize_ke(raw: str) -> str:
    s = raw.strip()
    digits = _DIGITS.sub("", s)
    if digits.startswith("254"):
        national = digits[3:]
    elif digits.startswith("0"):
        national = digits[1:]
    elif len(digits) == 9:
        national = digits
    else:
        raise InvalidPhone("unsupported number format")
    if not re.fullmatch(r"[17]\d{8}", national):
        raise InvalidPhone("not a Kenyan mobile number")
    return f"+254{national}"


def mask(e164: str) -> str:
    return f"{e164[:6]}***{e164[-2:]}" if len(e164) >= 9 else "***"


def same_number(a: str, b: str) -> bool:
    """Compare numbers regardless of '+'/format differences (providers echo them differently)."""
    try:
        return normalize_ke(a) == normalize_ke(b)
    except InvalidPhone:
        return False
