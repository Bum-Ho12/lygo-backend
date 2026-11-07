import base64
import os
import uuid

import pytest

from app.core import crypto
from app.core.config import settings
from app.core.crypto import (
    CryptoError,
    LocalKeyProvider,
    decrypt_secret,
    encrypt_secret,
    new_wrapped_dek,
)

F1, F2 = uuid.uuid4(), uuid.uuid4()


def provider() -> LocalKeyProvider:
    return LocalKeyProvider(base64.b64encode(os.urandom(32)).decode())


def test_roundtrip_and_nondeterminism() -> None:
    p = provider()
    dek = new_wrapped_dek(p, F1)
    a = encrypt_secret(p, dek, F1, "creds", b"secret-value")
    b = encrypt_secret(p, dek, F1, "creds", b"secret-value")
    assert a != b and b"secret-value" not in a  # fresh nonce every time; plaintext never visible
    assert decrypt_secret(p, dek, F1, "creds", a) == b"secret-value"


def test_ciphertext_is_bound_to_firm_purpose_and_integrity() -> None:
    p = provider()
    dek1, dek2 = new_wrapped_dek(p, F1), new_wrapped_dek(p, F2)
    blob = encrypt_secret(p, dek1, F1, "creds", b"x")
    with pytest.raises(CryptoError):  # another firm's key
        decrypt_secret(p, dek2, F2, "creds", blob)
    with pytest.raises(CryptoError):  # same key, wrong firm id in the AAD
        decrypt_secret(p, dek1, F2, "creds", blob)
    with pytest.raises(CryptoError):  # wrong purpose
        decrypt_secret(p, dek1, F1, "other", blob)
    tampered = bytearray(blob)
    tampered[-1] ^= 1
    with pytest.raises(CryptoError):
        decrypt_secret(p, dek1, F1, "creds", bytes(tampered))
    with pytest.raises(CryptoError):
        decrypt_secret(p, dek1, F1, "creds", b"short")


def test_wrapped_dek_unusable_with_a_different_master_key() -> None:
    dek = new_wrapped_dek(provider(), F1)
    with pytest.raises(CryptoError):
        provider().unwrap(dek, F1)  # crypto-shred / key rotation mismatch is detected, not silently wrong


def test_master_key_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CryptoError):
        LocalKeyProvider(base64.b64encode(b"too short").decode())
    monkeypatch.setattr(settings, "master_key", "")
    assert LocalKeyProvider()  # local dev fallback
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(CryptoError):
        LocalKeyProvider()  # never fall back to a derived key outside local
    assert crypto.get_key_provider  # dependency exists
