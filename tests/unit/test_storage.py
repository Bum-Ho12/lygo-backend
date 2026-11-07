import hashlib

import pytest
from moto import mock_aws

from app.storage.adapters.local_fs import LocalFsStorage
from app.storage.adapters.s3 import S3Storage
from app.storage.base import InvalidKey, ObjectNotFound, iter_bytes, validate_key

pytestmark = pytest.mark.asyncio
DATA = b"hello legal world" * 100_000  # ~1.7 MB -> multiple chunks


async def roundtrip(store) -> None:  # type: ignore[no-untyped-def]
    obj = await store.put("firm/doc/original", iter_bytes(DATA, 64 * 1024), content_type="text/plain", metadata={"k": "v"})
    assert obj.size == len(DATA) and obj.sha256 == hashlib.sha256(DATA).hexdigest()
    assert b"".join([c async for c in store.get("firm/doc/original")]) == DATA
    assert (await store.health_check()).ok
    await store.delete("firm/doc/original")
    with pytest.raises(ObjectNotFound):
        async for _ in store.get("firm/doc/original"):
            pass


async def test_local_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    await roundtrip(LocalFsStorage(tmp_path))
    assert not [p for p in tmp_path.rglob("*.tmp")]  # no temp files left behind


async def test_s3_roundtrip_and_presign() -> None:
    with mock_aws():
        store = S3Storage(bucket="lygo-test", region="us-east-1", access_key="x", secret_key="y", prefix="lygo")
        store._client.create_bucket(Bucket="lygo-test")
        await roundtrip(store)
        await store.put("a/b", iter_bytes(b"x"), content_type="text/plain", metadata={})
        assert "a/b" in (await store.presign_get("a/b", 60) or "") and "lygo" in (await store.presign_get("a/b") or "")


@pytest.mark.parametrize("key", ["", "/abs", "../x", "a/../b", "a//b", "a/./b", "a\\b", "a\x00b"])
def test_invalid_keys_rejected(key: str) -> None:
    with pytest.raises(InvalidKey):
        validate_key(key)


async def test_local_cannot_escape_root(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = LocalFsStorage(tmp_path / "root")
    with pytest.raises(InvalidKey):
        await store.put("../evil", iter_bytes(b"x"), content_type="x", metadata={})
    link = tmp_path / "root" / "link"
    link.symlink_to(tmp_path)  # symlink pointing outside the root
    with pytest.raises(InvalidKey):
        await store.put("link/evil", iter_bytes(b"x"), content_type="x", metadata={})
    assert not (tmp_path / "evil").exists()
