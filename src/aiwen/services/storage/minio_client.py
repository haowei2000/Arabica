"""MinIO client service for object storage operations."""

import io
import logging
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from aiwen.config.factory import get_settings

logger = logging.getLogger(__name__)

# Global MinIO client instance
_minio_client: Minio | None = None


def get_minio_client() -> Minio:
    """Get or create MinIO client instance.

    Returns:
        Minio: MinIO client instance.

    Raises:
        ValueError: If MinIO configuration is not set.
    """
    global _minio_client

    if _minio_client is not None:
        return _minio_client

    settings = get_settings()
    if not settings.minio:
        raise ValueError("MinIO configuration is not set")

    _minio_client = Minio(
        endpoint=settings.minio.endpoint,
        access_key=settings.minio.access_key,
        secret_key=settings.minio.secret_key,
        secure=settings.minio.secure,
    )
    logger.info(f"MinIO client initialized: {settings.minio.endpoint}")
    return _minio_client


class MinioClient:
    """MinIO client wrapper for common storage operations."""

    def __init__(self) -> None:
        """Initialize MinIO client."""
        self._client = get_minio_client()
        self._settings = get_settings()

    def ensure_bucket(self, bucket_name: str) -> None:
        """Ensure bucket exists, create if not.

        Args:
            bucket_name: Name of the bucket.
        """
        if not self._client.bucket_exists(bucket_name):
            self._client.make_bucket(bucket_name)
            logger.info(f"Created bucket: {bucket_name}")

    def download_file(self, bucket: str, object_key: str) -> bytes:
        """Download file from MinIO.

        Args:
            bucket: Bucket name.
            object_key: Object key/path in the bucket.

        Returns:
            bytes: File content as bytes.

        Raises:
            S3Error: If download fails.
        """
        try:
            response = self._client.get_object(bucket, object_key)
            data = response.read()
            response.close()
            response.release_conn()
            logger.info(f"Downloaded {object_key} from {bucket} ({len(data)} bytes)")
            return data
        except S3Error as e:
            logger.error(f"Failed to download {object_key} from {bucket}: {e}")
            raise

    def download_file_to_stream(self, bucket: str, object_key: str) -> BinaryIO:
        """Download file from MinIO as a stream.

        Args:
            bucket: Bucket name.
            object_key: Object key/path in the bucket.

        Returns:
            BinaryIO: File stream.
        """
        data = self.download_file(bucket, object_key)
        return io.BytesIO(data)

    def upload_file(
            self,
            bucket: str,
            object_key: str,
            data: bytes | BinaryIO,
            content_type: str = "application/octet-stream",
    ) -> str:
        """Upload file to MinIO.

        Args:
            bucket: Bucket name.
            object_key: Object key/path in the bucket.
            data: File content as bytes or file-like object.
            content_type: MIME type of the file.

        Returns:
            str: Object key of uploaded file.
        """
        self.ensure_bucket(bucket)

        if isinstance(data, bytes):
            data_stream = io.BytesIO(data)
            length = len(data)
        else:
            data_stream = data
            data_stream.seek(0, 2)
            length = data_stream.tell()
            data_stream.seek(0)

        self._client.put_object(
            bucket_name=bucket,
            object_name=object_key,
            data=data_stream,
            length=length,
            content_type=content_type,
        )
        logger.info(f"Uploaded {object_key} to {bucket} ({length} bytes)")
        return object_key

    def delete_file(self, bucket: str, object_key: str) -> None:
        """Delete file from MinIO.

        Args:
            bucket: Bucket name.
            object_key: Object key/path in the bucket.
        """
        try:
            self._client.remove_object(bucket, object_key)
            logger.info(f"Deleted {object_key} from {bucket}")
        except S3Error as e:
            logger.error(f"Failed to delete {object_key} from {bucket}: {e}")
            raise

    def file_exists(self, bucket: str, object_key: str) -> bool:
        """Check if file exists in MinIO.

        Args:
            bucket: Bucket name.
            object_key: Object key/path in the bucket.

        Returns:
            bool: True if file exists.
        """
        try:
            self._client.stat_object(bucket, object_key)
            return True
        except S3Error:
            return False

    def get_file_info(self, bucket: str, object_key: str) -> dict:
        """Get file metadata from MinIO.

        Args:
            bucket: Bucket name.
            object_key: Object key/path in the bucket.

        Returns:
            dict: File metadata including size, content_type, last_modified.
        """
        try:
            stat = self._client.stat_object(bucket, object_key)
            return {
                "size": stat.size,
                "content_type": stat.content_type,
                "last_modified": stat.last_modified,
                "etag": stat.etag,
            }
        except S3Error as e:
            logger.error(f"Failed to get info for {object_key} from {bucket}: {e}")
            raise
