from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    etag: str | None = None
    content_type: str | None = None
    last_modified: datetime | None = None
    metadata: dict | None = None


class StorageBackend(ABC):
    @abstractmethod
    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict | None = None,
        overwrite: bool = True,
    ) -> ObjectInfo: ...

    @abstractmethod
    def put_file(
        self,
        key: str,
        file: BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict | None = None,
        overwrite: bool = True,
    ) -> ObjectInfo: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def stat(self, key: str) -> ObjectInfo: ...

    @abstractmethod
    def list(self, prefix: str = "") -> Iterable[ObjectInfo]: ...

    def get_url(self, key: str, expires_seconds: int = 3600) -> str:
        raise NotImplementedError


def _normalize_key(key: str) -> str:
    return key.lstrip("/")


class S3StorageBackend(StorageBackend):
    """
    RustFS / MinIO / S3 通用实现（boto3）
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region_name: str = "us-east-1",
        use_ssl: bool = False,
    ):
        self.bucket = bucket

        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name,
            use_ssl=use_ssl,
            config=Config(
                s3={"addressing_style": "path"},  # RustFS/MinIO 常用 path style
                retries={"max_attempts": 3},
            ),
        )

    def ensure_bucket(self) -> None:
        """
        确保 bucket 存在，不存在则创建。
        兼容 RustFS / MinIO / S3。
        """
        try:
            self.s3.head_bucket(Bucket=self.bucket)
            return
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            # 常见：404 / NoSuchBucket / NotFound
            if code not in ("404", "NoSuchBucket", "NotFound"):
                raise

        # RustFS/MinIO 一般不需要 CreateBucketConfiguration
        try:
            self.s3.create_bucket(Bucket=self.bucket)
        except ClientError as e:
            # 并发场景可能别人已经创建了
            code = e.response.get("Error", {}).get("Code")
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                return
            raise

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict | None = None,
        overwrite: bool = True,
    ) -> ObjectInfo:
        key = _normalize_key(key)

        if not overwrite and self.exists(key):
            raise FileExistsError(key)

        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        if metadata:
            extra["Metadata"] = metadata

        resp = self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            **extra,
        )

        etag = resp.get("ETag", "").strip('"') if resp else None
        info = self.stat(key)
        return ObjectInfo(
            key=info.key,
            size=info.size,
            etag=etag or info.etag,
            content_type=info.content_type,
            last_modified=info.last_modified,
            metadata=info.metadata,
        )

    def put_file(
        self,
        key: str,
        file: BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict | None = None,
        overwrite: bool = True,
    ) -> ObjectInfo:
        key = _normalize_key(key)

        if not overwrite and self.exists(key):
            raise FileExistsError(key)

        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        if metadata:
            extra["Metadata"] = metadata

        # upload_fileobj 会自动分片上传（大文件友好）
        self.s3.upload_fileobj(
            Fileobj=file,
            Bucket=self.bucket,
            Key=key,
            ExtraArgs=extra if extra else None,
        )
        return self.stat(key)

    def get_bytes(self, key: str) -> bytes:
        key = _normalize_key(key)
        resp = self.s3.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read()

    def delete(self, key: str) -> None:
        key = _normalize_key(key)
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        key = _normalize_key(key)
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def stat(self, key: str) -> ObjectInfo:
        key = _normalize_key(key)
        resp = self.s3.head_object(Bucket=self.bucket, Key=key)

        return ObjectInfo(
            key=key,
            size=int(resp.get("ContentLength", 0)),
            etag=(resp.get("ETag") or "").strip('"') or None,
            content_type=resp.get("ContentType"),
            last_modified=resp.get("LastModified"),
            metadata=resp.get("Metadata") or None,
        )

    def list(self, prefix: str = "") -> Iterable[ObjectInfo]:
        prefix = _normalize_key(prefix)

        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield ObjectInfo(
                    key=item["Key"],
                    size=int(item.get("Size", 0)),
                    etag=(item.get("ETag") or "").strip('"') or None,
                    last_modified=item.get("LastModified"),
                )

    def get_url(self, key: str, expires_seconds: int = 3600) -> str:
        key = _normalize_key(key)
        return self.s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )
