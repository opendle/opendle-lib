"""Content-addressed blob storage in an S3 bucket with a bounded local cache.

The module has no dependency. The caller supplies an S3 client, for example
`boto3.client("s3", ...)`, that satisfies `ObjectClient`.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = ["Blob", "BlobStore", "ObjectClient"]

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_READ_BYTES = 1_048_576
_TEMPORARY_PREFIX = "tmp-"


class ObjectClient(Protocol):
    """Name the S3 client operations that `BlobStore` uses."""

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None:  # noqa: N803
        """Upload a local file to an object."""
        ...

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        """Download an object to a local file."""
        ...

    def list_objects_v2(
        self,
        *,
        Bucket: str,  # noqa: N803
        Prefix: str,  # noqa: N803
        MaxKeys: int,  # noqa: N803
    ) -> Mapping[str, object]:
        """List objects by prefix. The result has the `KeyCount` field."""
        ...


@runtime_checkable
class _DeleteClient(Protocol):
    """Name the optional S3 delete operation."""

    def delete_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:  # noqa: N803
        """Delete one object. A missing object has the same result."""
        ...


@dataclass(frozen=True, slots=True)
class Blob:
    """Identify one stored blob by the SHA-256 of its content."""

    sha256: str
    size: int


class BlobStore:
    """Store blobs by SHA-256 and keep recent blobs in a local cache."""

    def __init__(
        self,
        client: ObjectClient,
        *,
        bucket: str,
        cache_directory: Path,
        cache_maximum_bytes: int,
        key_prefix: str = "sha256/",
    ) -> None:
        """Use one bucket and one cache directory with a byte limit.

        The store creates the cache directory. The store must be the only
        writer of that directory.
        """
        if cache_maximum_bytes < 0:
            msg = "The cache byte limit must not be negative."
            raise ValueError(msg)
        self._client = client
        self._bucket = bucket
        self._cache_directory = cache_directory
        self._cache_maximum_bytes = cache_maximum_bytes
        self._key_prefix = key_prefix
        cache_directory.mkdir(parents=True, exist_ok=True)

    def put_file(self, source: Path) -> Blob:
        """Store a local file and keep a copy in the cache.

        The function uploads the content only when the bucket does not have
        it. The source file stays in its position.
        """
        temporary = self._temporary_path()
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as reader, temporary.open("wb") as writer:
                while chunk := reader.read(_READ_BYTES):
                    digest.update(chunk)
                    writer.write(chunk)
                    size += len(chunk)
            sha256 = digest.hexdigest()
            listing = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=self._key(sha256),
                MaxKeys=1,
            )
            if not listing.get("KeyCount"):
                self._client.upload_file(
                    str(temporary),
                    self._bucket,
                    self._key(sha256),
                )
            temporary.replace(self._cache_path(sha256))
        finally:
            temporary.unlink(missing_ok=True)
        self._trim(keep=sha256)
        return Blob(sha256=sha256, size=size)

    def local_path(self, sha256: str) -> Path:
        """Return the cache path of a blob. Download the blob when necessary.

        The path stays valid until a later call of this store removes it from
        the cache. Read or copy the file before that.

        Raises:
            ValueError: If the SHA-256 value does not have the correct format.

        """
        path = self._cache_path(sha256)
        if path.exists():
            os.utime(path)
        else:
            temporary = self._temporary_path()
            try:
                self._client.download_file(
                    self._bucket,
                    self._key(sha256),
                    str(temporary),
                )
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        self._trim(keep=sha256)
        return path

    def is_cached(self, sha256: str) -> bool:
        """Return whether the cache has a copy of a blob."""
        return self._cache_path(sha256).exists()

    def drop_local(self, sha256: str) -> None:
        """Delete the cache copy of a blob. The bucket object stays."""
        self._cache_path(sha256).unlink(missing_ok=True)

    def delete(self, sha256: str) -> None:
        """Delete a bucket object and its local cache copy.

        The S3 client must provide `delete_object(Bucket=..., Key=...)`.
        The operation is safe to repeat when the object is absent. The caller
        must ensure that no reader or writer needs the blob. A bucket error
        propagates and keeps the local copy.

        Raises:
            ValueError: If the blob ID is not a lowercase SHA-256 value.
            TypeError: If the client does not provide object deletion.

        """
        path = self._cache_path(sha256)
        if not isinstance(self._client, _DeleteClient):
            msg = "The object client does not support deletion."
            raise TypeError(msg)
        self._client.delete_object(Bucket=self._bucket, Key=self._key(sha256))
        path.unlink(missing_ok=True)

    def copy_to(self, sha256: str, target: Path) -> None:
        """Copy a blob to a local file outside the cache."""
        shutil.copyfile(self.local_path(sha256), target)

    def _key(self, sha256: str) -> str:
        return f"{self._key_prefix}{sha256}"

    def _cache_path(self, sha256: str) -> Path:
        if _SHA256_PATTERN.fullmatch(sha256) is None:
            msg = "The blob ID must be a lowercase SHA-256 hexadecimal value."
            raise ValueError(msg)
        return self._cache_directory / sha256

    def _temporary_path(self) -> Path:
        return self._cache_directory / f"{_TEMPORARY_PREFIX}{uuid.uuid4().hex}"

    def _trim(self, *, keep: str) -> None:
        entries = sorted(
            (
                (entry.stat().st_mtime_ns, entry.stat().st_size, entry)
                for entry in self._cache_directory.iterdir()
                if not entry.name.startswith(_TEMPORARY_PREFIX)
            ),
            key=lambda item: item[0],
        )
        total = sum(size for _, size, _ in entries)
        for _, size, entry in entries:
            if total <= self._cache_maximum_bytes:
                break
            if entry.name != keep:
                entry.unlink(missing_ok=True)
                total -= size
