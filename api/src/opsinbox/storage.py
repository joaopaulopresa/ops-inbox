"""Object storage behind an interface: MinIO in the stack, memory in tests.

Objects are immutable once written. Keys are content-addressed where possible, so writing the same
bytes twice is harmless and a crashed step can simply run again.
"""

import hashlib
import io
from functools import lru_cache
from typing import Protocol

from minio import Minio
from minio.error import S3Error

from opsinbox.errors import ObjectNotFoundError, StorageError
from opsinbox.settings import get_settings


def sha256_hex(data: bytes) -> str:
    """Return the hexadecimal SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


class ObjectStore(Protocol):
    """What the application needs from an object store."""

    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store ``data`` under ``key``. Writing an existing key again is a no-op."""

    def get(self, key: str) -> bytes:
        """Return the bytes stored under ``key`` or raise :class:`ObjectNotFoundError`."""

    def exists(self, key: str) -> bool:
        """Tell whether ``key`` is stored."""


class MemoryObjectStore:
    """In-process store used by tests and the evaluation harness."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.put_calls = 0

    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store ``data`` unless the key is already taken."""
        self.put_calls += 1
        self.objects.setdefault(key, (data, content_type))

    def get(self, key: str) -> bytes:
        """Return the stored bytes."""
        try:
            return self.objects[key][0]
        except KeyError as exc:
            raise ObjectNotFoundError(key) from exc

    def exists(self, key: str) -> bool:
        """Tell whether ``key`` is stored."""
        return key in self.objects


class MinioObjectStore:
    """S3-compatible store (MinIO in Docker Compose)."""

    def __init__(
        self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool
    ) -> None:
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket
        self._bucket_ready = False

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
        except S3Error as exc:
            if exc.code != "BucketAlreadyOwnedByYou":
                raise StorageError(str(exc)) from exc
        self._bucket_ready = True

    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store ``data`` unless the key is already taken (objects are immutable)."""
        self._ensure_bucket()
        if self.exists(key):
            return
        try:
            self._client.put_object(
                self._bucket, key, io.BytesIO(data), len(data), content_type=content_type
            )
        except S3Error as exc:
            raise StorageError(str(exc)) from exc

    def get(self, key: str) -> bytes:
        """Return the stored bytes."""
        self._ensure_bucket()
        try:
            response = self._client.get_object(self._bucket, key)
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                raise ObjectNotFoundError(key) from exc
            raise StorageError(str(exc)) from exc
        try:
            return bytes(response.read())
        finally:
            response.close()
            response.release_conn()

    def exists(self, key: str) -> bool:
        """Tell whether ``key`` is stored."""
        self._ensure_bucket()
        try:
            self._client.stat_object(self._bucket, key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                return False
            raise StorageError(str(exc)) from exc
        return True


@lru_cache
def get_object_store() -> ObjectStore:
    """Return the process-wide object store built from settings."""
    s = get_settings()
    return MinioObjectStore(
        s.s3_endpoint, s.s3_access_key, s.s3_secret_key, s.s3_bucket, s.s3_secure
    )
