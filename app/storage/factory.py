from functools import lru_cache

from app.core.config import settings
from app.storage.adapters.local_fs import LocalFsStorage
from app.storage.adapters.s3 import S3Storage
from app.storage.base import StorageDestination


@lru_cache
def default_destination() -> StorageDestination:
    """Lygo-managed destination. Per-firm BYO destinations come with storage policies (docs/06)."""
    if settings.storage_backend == "s3":
        return S3Storage(
            bucket=settings.storage_bucket, endpoint_url=settings.storage_endpoint or None, region=settings.storage_region,
            access_key=settings.storage_access_key or None, secret_key=settings.storage_secret_key or None,
            destination_id="lygo-managed",
        )
    return LocalFsStorage(settings.storage_local_dir, destination_id="lygo-managed")


def get_storage() -> StorageDestination:
    """FastAPI dependency; override in tests."""
    return default_destination()
