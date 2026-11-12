"""Object storage for the big, cold things: logs, diffs, trajectories.

None of this belongs in Postgres. A 50-task suite at k=5 produces a few hundred
megabytes of container diffs and transcripts per run; put those in the database
and every backup, replica and ``pg_dump`` carries them forever, while the
queries you actually run get slower.

Two backends behind one interface: S3/MinIO for deployments, a local directory
so a laptop needs no object store. Presigned URLs expire in fifteen minutes and
are scoped per object.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from autobench.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: Compressed above this; trajectories and diffs are highly repetitive text.
GZIP_THRESHOLD = 16 * 1024


@dataclass(slots=True)
class StoredObject:
    key: str
    size_bytes: int
    content_type: str
    compressed: bool = False
    truncated: bool = False


class ArtifactStore(Protocol):
    async def put(
        self, key: str, data: bytes | str, *, content_type: str | None = None
    ) -> StoredObject: ...
    async def get(self, key: str) -> bytes: ...
    async def url(self, key: str, *, ttl: int | None = None) -> str: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    async def close(self) -> None: ...


def trial_key(trial_id: str, name: str) -> str:
    """``trials/<prefix>/<trial>/<name>``.

    The two-character shard keeps any one S3 prefix from becoming a hotspot,
    and keeps the local directory from having a million entries in one folder.
    """
    flat = str(trial_id).replace("-", "")
    return f"trials/{flat[:2]}/{flat}/{name}"


def _prepare(data: bytes | str, content_type: str | None, key: str) -> tuple[bytes, str, bool]:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    resolved = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
    if len(raw) >= GZIP_THRESHOLD:
        return gzip.compress(raw, compresslevel=6), resolved, True
    return raw, resolved, False


class LocalArtifactStore:
    """Files under ``state_dir/artifacts``. The zero-infrastructure backend."""

    def __init__(self, root: Path, base_url: str) -> None:
        self.root = root
        self.base_url = base_url.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root.resolve()):
            raise ValueError(f"artifact key escapes the store root: {key!r}")
        return candidate

    async def put(
        self, key: str, data: bytes | str, *, content_type: str | None = None
    ) -> StoredObject:
        payload, resolved, compressed = _prepare(data, content_type, key)
        path = self._path(key + (".gz" if compressed else ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, payload)
        return StoredObject(key, len(payload), resolved, compressed)

    async def get(self, key: str) -> bytes:
        gz = self._path(key + ".gz")
        if gz.is_file():
            return gzip.decompress(await asyncio.to_thread(gz.read_bytes))
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def url(self, key: str, *, ttl: int | None = None) -> str:
        """Served by the API rather than presigned — there is no signer here."""
        return f"{self.base_url}/v1/artifacts/{quote(key)}"

    async def delete_prefix(self, prefix: str) -> int:
        directory = self._path(prefix)
        if not directory.exists():
            return 0
        removed = 0
        for path in directory.rglob("*"):
            if path.is_file():
                path.unlink()
                removed += 1
        return removed

    async def close(self) -> None:
        return None


class S3ArtifactStore:
    """MinIO or S3. boto3 is synchronous, so every call goes to a thread."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.s3_bucket
        self._client: Any = None

    def _boto(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            credentials = {}
            if self.settings.s3_access_key and self.settings.s3_secret_key:
                credentials = {
                    "aws_access_key_id": self.settings.s3_access_key,
                    "aws_secret_access_key": self.settings.s3_secret_key,
                }
            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.s3_endpoint_url,
                region_name=self.settings.s3_region,
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 5, "mode": "adaptive"},
                    # MinIO does not do virtual-host addressing by default.
                    s3={"addressing_style": "path"},
                ),
                **credentials,
            )
            if self.settings.s3_create_bucket:
                self._ensure_bucket()
        return self._client

    def _ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                self._client.create_bucket(Bucket=self.bucket)
                logger.info("created artifact bucket %s", self.bucket)
            except Exception as exc:  # pragma: no cover
                logger.warning("could not create bucket %s: %s", self.bucket, exc)

    async def put(
        self, key: str, data: bytes | str, *, content_type: str | None = None
    ) -> StoredObject:
        payload, resolved, compressed = _prepare(data, content_type, key)
        extra = {"ContentType": resolved}
        if compressed:
            extra["ContentEncoding"] = "gzip"

        def _upload() -> None:
            self._boto().put_object(Bucket=self.bucket, Key=key, Body=payload, **extra)

        await asyncio.to_thread(_upload)
        return StoredObject(key, len(payload), resolved, compressed)

    async def get(self, key: str) -> bytes:
        def _download() -> bytes:
            response = self._boto().get_object(Bucket=self.bucket, Key=key)
            body = response["Body"].read()
            if response.get("ContentEncoding") == "gzip":
                return gzip.decompress(body)
            return bytes(body)

        return await asyncio.to_thread(_download)

    async def url(self, key: str, *, ttl: int | None = None) -> str:
        expiry = ttl or self.settings.presign_ttl_sec

        def _sign() -> str:
            return str(
                self._boto().generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key},
                    ExpiresIn=expiry,
                )
            )

        return await asyncio.to_thread(_sign)

    async def delete_prefix(self, prefix: str) -> int:
        def _delete() -> int:
            client = self._boto()
            paginator = client.get_paginator("list_objects_v2")
            removed = 0
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if keys:
                    client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
                    removed += len(keys)
            return removed

        return await asyncio.to_thread(_delete)

    async def close(self) -> None:
        self._client = None


_store: ArtifactStore | None = None


def get_store(settings: Settings | None = None) -> ArtifactStore:
    global _store
    if _store is None:
        settings = settings or get_settings()
        if settings.artifact_backend == "s3":
            _store = S3ArtifactStore(settings)
        else:
            _store = LocalArtifactStore(settings.artifacts_dir, settings.api_base_url)
    return _store


def set_store(store: ArtifactStore | None) -> None:
    global _store
    _store = store
