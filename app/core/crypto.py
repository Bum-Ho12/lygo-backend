"""Envelope encryption. See docs/06 (BYO credentials) and docs/07.

Each firm has its own random data key (DEK). The DEK is stored only *wrapped* by a master key that comes from a
`KeyProvider` (env key locally; KMS/Vault in production). Secrets are sealed with AES-256-GCM under the firm DEK, bound
to the firm and a purpose via AAD. Deleting a firm's wrapped DEK ("crypto-shredding") makes its secrets unrecoverable.
"""
import base64
import hashlib
import os
import uuid
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_VERSION = b"\x01"


class CryptoError(Exception): ...


class KeyProvider(Protocol):
    def wrap(self, dek: bytes, firm_id: uuid.UUID) -> bytes: ...
    def unwrap(self, wrapped: bytes, firm_id: uuid.UUID) -> bytes: ...


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    return _VERSION + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) < 30 or blob[:1] != _VERSION:
        raise CryptoError("unrecognised ciphertext")
    try:
        return AESGCM(key).decrypt(blob[1:13], blob[13:], aad)
    except InvalidTag as exc:
        raise CryptoError("decryption failed (wrong key, wrong firm, or tampered data)") from exc


class LocalKeyProvider:
    """Master key from MASTER_KEY (base64, 32 bytes). Locally falls back to a key derived from JWT_SECRET (dev only)."""

    def __init__(self, master_key_b64: str | None = None) -> None:
        raw = master_key_b64 if master_key_b64 is not None else settings.master_key
        if raw:
            key = base64.b64decode(raw)
            if len(key) != 32:
                raise CryptoError("MASTER_KEY must decode to exactly 32 bytes")
        elif settings.is_local:
            key = hashlib.sha256(f"lygo-dev-master:{settings.jwt_secret}".encode()).digest()
        else:
            raise CryptoError("MASTER_KEY is required outside local env")
        self._key = key

    def wrap(self, dek: bytes, firm_id: uuid.UUID) -> bytes:
        return _seal(self._key, dek, b"dek:" + firm_id.bytes)

    def unwrap(self, wrapped: bytes, firm_id: uuid.UUID) -> bytes:
        return _open(self._key, wrapped, b"dek:" + firm_id.bytes)


def new_wrapped_dek(provider: KeyProvider, firm_id: uuid.UUID) -> bytes:
    return provider.wrap(os.urandom(32), firm_id)


def encrypt_secret(provider: KeyProvider, wrapped_dek: bytes, firm_id: uuid.UUID, purpose: str, plaintext: bytes) -> bytes:
    return _seal(provider.unwrap(wrapped_dek, firm_id), plaintext, f"{purpose}:".encode() + firm_id.bytes)


def decrypt_secret(provider: KeyProvider, wrapped_dek: bytes, firm_id: uuid.UUID, purpose: str, blob: bytes) -> bytes:
    return _open(provider.unwrap(wrapped_dek, firm_id), blob, f"{purpose}:".encode() + firm_id.bytes)


def get_key_provider() -> KeyProvider:
    """FastAPI dependency; override in tests."""
    return LocalKeyProvider()
