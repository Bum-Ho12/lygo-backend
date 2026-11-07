"""Password hashing, access tokens and opaque refresh tokens. See docs/07-security-and-compliance.md."""
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings

_hasher = PasswordHasher()  # Argon2id with library defaults
# Verified against when the account does not exist, so login timing does not reveal valid emails.
_DUMMY_HASH = _hasher.hash("lygo-dummy-password")
ALGORITHM = "HS256"
MIN_PASSWORD_LEN = 12


class InvalidToken(Exception): ...


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        ok = _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return ok and password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    mfa: bool  # True once a second factor was verified for this session


def create_access_token(user_id: uuid.UUID, ttl: timedelta | None = None, *, mfa: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id), "iat": now, "exp": now + (ttl or timedelta(minutes=settings.jwt_access_ttl_min)),
        "jti": uuid.uuid4().hex, "typ": "access", "mfa": mfa,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_claims(token: str) -> AccessClaims:
    try:
        data = jwt.decode(
            token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "iat", "sub"]}
        )
        if data.get("typ") != "access":
            raise InvalidToken("wrong token type")
        return AccessClaims(uuid.UUID(data["sub"]), bool(data.get("mfa", False)))
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidToken(str(exc)) from exc


def decode_access_token(token: str) -> uuid.UUID:
    return decode_access_claims(token).user_id


MFA_TOKEN_TTL = timedelta(minutes=5)


def create_mfa_token(user_id: uuid.UUID) -> str:
    """Short-lived proof that the password step succeeded; useless as an access token (typ differs)."""
    now = datetime.now(UTC)
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + MFA_TOKEN_TTL, "typ": "mfa", "jti": uuid.uuid4().hex}, settings.jwt_secret, algorithm=ALGORITHM)


def decode_mfa_token(token: str) -> uuid.UUID:
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "iat", "sub"]})
        if data.get("typ") != "mfa":
            raise InvalidToken("wrong token type")
        return uuid.UUID(data["sub"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidToken(str(exc)) from exc


def keyed_hash(purpose: str, value: str) -> str:
    """HMAC-SHA256 under a key derived from JWT_SECRET: for codes that are short and guessable (OTP, recovery codes), where an
    unkeyed hash in the database could be brute-forced offline."""
    key = hashlib.sha256(f"lygo-{purpose}:{settings.jwt_secret}".encode()).digest()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """Return (plaintext for the client, sha256 hex for storage)."""
    plain = secrets.token_urlsafe(48)
    return plain, hash_refresh_token(plain)


def hash_refresh_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()
