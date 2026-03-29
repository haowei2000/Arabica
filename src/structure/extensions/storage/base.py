from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    etag: str | None = None
    content_type: str | None = None
    last_modified: datetime | None = None
    metadata: dict | None = None


class StorageBackend(ABC):
    """
    统一存储接口（本地 / MinIO / S3 / RustFS / OSS ...）
    key: 逻辑路径，例如: "tenantA/user1/a.pdf"
    """

    @abstractmethod
    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict | None = None,
        overwrite: bool = True,
    ) -> ObjectInfo:
        """上传 bytes"""

    @abstractmethod
    def put_file(
        self,
        key: str,
        file: BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict | None = None,
        overwrite: bool = True,
    ) -> ObjectInfo:
        """上传文件流（适合大文件）"""

    @abstractmethod
    def get_bytes(self, key: str) -> bytes:
        """下载为 bytes"""

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """返回一个可读的二进制流（需要调用方 close）"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除对象"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """对象是否存在"""

    @abstractmethod
    def stat(self, key: str) -> ObjectInfo:
        """获取对象元信息"""

    @abstractmethod
    def list(self, prefix: str = "") -> Iterable[ObjectInfo]:
        """列出 prefix 下对象"""

    def search(self, keyword: str, prefix: str = "") -> Iterable[ObjectInfo]:
        """
        简单检索（默认实现：基于 list + key 包含匹配）
        复杂检索建议上索引系统（ES/PG/SQLite）
        """
        for obj in self.list(prefix=prefix):
            if keyword in obj.key:
                yield obj

    def get_url(self, key: str, expires_seconds: int = 3600) -> str:
        """
        可选：生成临时访问 URL（本地实现可以返回一个下载接口 URL）
        """
        raise NotImplementedError
