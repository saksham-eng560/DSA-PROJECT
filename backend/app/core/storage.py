"""Blob storage for resume PDFs and screenshots (local filesystem or S3 / Cloudflare R2)."""

from __future__ import annotations

import logging
import mimetypes
import shutil
from pathlib import Path
from typing import Protocol

from app.config import settings

logger = logging.getLogger(__name__)


class Storage(Protocol):
    def save(self, key: str, data: bytes, content_type: str | None = None) -> str: ...
    def read(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...
    def presigned_url(self, key: str, expires: int = 3600) -> str | None: ...


def _safe_key(key: str) -> str:
    parts = [p for p in key.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _safe_key(key)).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError("Invalid storage key")
        return path

    def save(self, key: str, data: bytes, content_type: str | None = None) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return _safe_key(key)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()

    def presigned_url(self, key: str, expires: int = 3600) -> str | None:
        return None


class S3Storage:
    def __init__(self) -> None:
        import boto3

        self.bucket = settings.S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        )

    def save(self, key: str, data: bytes, content_type: str | None = None) -> str:
        key = _safe_key(key)
        ctype = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        extra = {}
        if not settings.S3_ENDPOINT_URL:  # AWS S3 (R2/MinIO encrypt by default / reject the header)
            extra["ServerSideEncryption"] = "AES256"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=ctype, **extra)
        return key

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_safe_key(key))
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_safe_key(key))

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=_safe_key(prefix)):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})

    def presigned_url(self, key: str, expires: int = 3600) -> str | None:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": _safe_key(key)}, ExpiresIn=expires
        )


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        if settings.STORAGE_BACKEND == "s3" and settings.S3_BUCKET:
            _storage = S3Storage()
        else:
            _storage = LocalStorage(settings.LOCAL_STORAGE_PATH)
    return _storage


def set_storage(storage: Storage | None) -> None:
    global _storage
    _storage = storage


def user_prefix(user_id: object) -> str:
    return f"users/{user_id}"
