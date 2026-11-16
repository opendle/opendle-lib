"""Check the content-addressed blob store with a fake bucket client."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from opendle.blob_store import BlobStore

if TYPE_CHECKING:
    from collections.abc import Mapping


class FakeBucket:
    """Keep objects in memory and count the transfers."""

    def __init__(self) -> None:
        """Start with an empty bucket."""
        self.objects: dict[tuple[str, str], bytes] = {}
        self.uploads = 0
        self.downloads = 0

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None:  # noqa: N803
        """Store a local file as an object."""
        self.uploads += 1
        self.objects[Bucket, Key] = Path(Filename).read_bytes()

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        """Write an object to a local file. A missing object is an error."""
        self.downloads += 1
        Path(Filename).write_bytes(b"partial")
        Path(Filename).write_bytes(self.objects[Bucket, Key])

    def delete_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:  # noqa: N803
        """Remove an object if it exists."""
        self.objects.pop((Bucket, Key), None)
        return {}

    def list_objects_v2(
        self,
        *,
        Bucket: str,  # noqa: N803
        Prefix: str,  # noqa: N803
        MaxKeys: int,  # noqa: N803
    ) -> Mapping[str, object]:
        """Count the objects with a prefix."""
        keys = [key for key in self.objects if key == (Bucket, Prefix)]
        return {"KeyCount": len(keys[:MaxKeys])}


def _store(tmp_path: Path, bucket: FakeBucket, limit: int = 100) -> BlobStore:
    return BlobStore(
        bucket,
        bucket="media",
        cache_directory=tmp_path / "cache",
        cache_maximum_bytes=limit,
    )


def _source(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / f"source-{len(content)}-{content[:1].hex()}"
    path.write_bytes(content)
    return path


def _age(tmp_path: Path, sha256: str, seconds: int) -> None:
    os.utime(tmp_path / "cache" / sha256, (seconds, seconds))


def test_a_file_is_stored_one_time_by_its_sha256(tmp_path: Path) -> None:
    """The second put of the same content does not upload again."""
    bucket = FakeBucket()
    store = _store(tmp_path, bucket)
    source = _source(tmp_path, b"hello")
    sha256 = hashlib.sha256(b"hello").hexdigest()

    blob = store.put_file(source)
    again = store.put_file(source)

    assert (blob.sha256, blob.size) == (sha256, 5)
    assert again == blob
    assert bucket.objects == {("media", f"sha256/{sha256}"): b"hello"}
    assert bucket.uploads == 1
    assert source.read_bytes() == b"hello"
    assert store.is_cached(sha256)
    assert sorted(path.name for path in (tmp_path / "cache").iterdir()) == [sha256]


def test_a_blob_comes_from_the_cache_or_from_the_bucket(tmp_path: Path) -> None:
    """A dropped local copy is downloaded again on the next read."""
    bucket = FakeBucket()
    store = _store(tmp_path, bucket)
    blob = store.put_file(_source(tmp_path, b"hello"))

    assert store.local_path(blob.sha256).read_bytes() == b"hello"
    assert bucket.downloads == 0

    store.drop_local(blob.sha256)
    store.drop_local(blob.sha256)
    assert not store.is_cached(blob.sha256)

    target = tmp_path / "copy"
    store.copy_to(blob.sha256, target)
    assert target.read_bytes() == b"hello"
    assert bucket.downloads == 1
    assert store.is_cached(blob.sha256)


def test_a_failed_download_leaves_no_file(tmp_path: Path) -> None:
    """A missing object is an error and the cache stays empty."""
    store = _store(tmp_path, FakeBucket())

    with pytest.raises(KeyError):
        store.local_path("0" * 64)

    assert list((tmp_path / "cache").iterdir()) == []


def test_the_cache_deletes_the_oldest_blobs_above_its_limit(tmp_path: Path) -> None:
    """The limit removes old blobs first and never the blob in use."""
    bucket = FakeBucket()
    store = _store(tmp_path, bucket, limit=10)
    old = store.put_file(_source(tmp_path, b"a" * 4))
    middle = store.put_file(_source(tmp_path, b"b" * 4))
    _age(tmp_path, old.sha256, 1000)
    _age(tmp_path, middle.sha256, 2000)
    (tmp_path / "cache" / "tmp-other-writer").write_bytes(b"x" * 50)

    new = store.put_file(_source(tmp_path, b"c" * 4))

    assert not store.is_cached(old.sha256)
    assert store.is_cached(middle.sha256)
    assert store.is_cached(new.sha256)
    assert (tmp_path / "cache" / "tmp-other-writer").exists()

    _age(tmp_path, new.sha256, 3000)
    store.local_path(middle.sha256)
    big = store.put_file(_source(tmp_path, b"d" * 6))

    assert not store.is_cached(new.sha256)
    assert store.is_cached(middle.sha256)
    assert store.is_cached(big.sha256)


def test_a_blob_larger_than_the_cache_stays_while_it_is_in_use(
    tmp_path: Path,
) -> None:
    """The blob of the current call stays and all other blobs go."""
    store = _store(tmp_path, FakeBucket(), limit=0)
    first = store.put_file(_source(tmp_path, b"a"))
    _age(tmp_path, first.sha256, 1000)
    second = store.put_file(_source(tmp_path, b"bb"))

    assert not store.is_cached(first.sha256)
    assert store.is_cached(second.sha256)

    _age(tmp_path, second.sha256, 1000)
    assert store.local_path(first.sha256).read_bytes() == b"a"
    assert not store.is_cached(second.sha256)


def test_bad_input_is_refused(tmp_path: Path) -> None:
    """A negative limit or a bad blob ID is a caller error."""
    with pytest.raises(ValueError, match="negative"):
        _store(tmp_path, FakeBucket(), limit=-1)
    with pytest.raises(ValueError, match="SHA-256"):
        _store(tmp_path, FakeBucket()).local_path("../secret")


def test_delete_removes_the_object_and_cache_and_can_be_repeated(
    tmp_path: Path,
) -> None:
    """Other blobs stay available, and a removed blob can be uploaded again."""
    bucket = FakeBucket()
    store = _store(tmp_path, bucket)
    first = store.put_file(_source(tmp_path, b"first"))
    second = store.put_file(_source(tmp_path, b"second"))

    store.delete(first.sha256)
    store.delete(first.sha256)

    assert ("media", f"sha256/{first.sha256}") not in bucket.objects
    assert not store.is_cached(first.sha256)
    assert store.local_path(second.sha256).read_bytes() == b"second"
    store.put_file(_source(tmp_path, b"first"))
    assert bucket.objects["media", f"sha256/{first.sha256}"] == b"first"


def test_delete_rejects_bad_ids_and_keeps_the_cache_on_bucket_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed bucket delete must not remove the only usable local copy."""
    bucket = FakeBucket()
    store = _store(tmp_path, bucket)
    blob = store.put_file(_source(tmp_path, b"first"))

    with pytest.raises(ValueError, match="SHA-256"):
        store.delete("../other")
    assert len(bucket.objects) == 1

    def fail_delete(*, Bucket: str, Key: str) -> Mapping[str, object]:  # noqa: N803
        assert Bucket == "media"
        assert Key.startswith("sha256/")
        message = "The bucket did not answer."
        raise OSError(message)

    monkeypatch.setattr(bucket, "delete_object", fail_delete)
    with pytest.raises(OSError, match="did not answer"):
        store.delete(blob.sha256)
    assert store.local_path(blob.sha256).read_bytes() == b"first"
    assert len(bucket.objects) == 1


def test_delete_requires_an_optional_delete_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing client can still use the store without deletion support."""
    bucket = FakeBucket()
    store = _store(tmp_path, bucket)
    blob = store.put_file(_source(tmp_path, b"first"))
    legacy = SimpleNamespace(
        upload_file=bucket.upload_file,
        download_file=bucket.download_file,
        list_objects_v2=bucket.list_objects_v2,
    )
    monkeypatch.setattr(store, "_client", legacy)

    with pytest.raises(TypeError, match="does not support deletion"):
        store.delete(blob.sha256)
    assert store.local_path(blob.sha256).read_bytes() == b"first"
