from .s3_storage_backend import S3StorageBackend

# 全局存储实例
_global_s3_storage: S3StorageBackend | None = None


def init_global_s3_storage(
    endpoint_url: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    region_name: str = "us-east-1",
    use_ssl: bool = False,
) -> None:
    """
    初始化全局 S3 存储实例
    """
    global _global_s3_storage
    _global_s3_storage = S3StorageBackend(
        endpoint_url=endpoint_url,
        bucket=bucket,
        access_key=access_key,
        secret_key=secret_key,
        region_name=region_name,
        use_ssl=use_ssl,
    )
    # 确保存储桶存在
    _global_s3_storage.ensure_bucket()


def get_global_s3_storage() -> S3StorageBackend:
    """
    获取全局 S3 存储实例
    """
    global _global_s3_storage
    if _global_s3_storage is None:
        raise RuntimeError("Global S3 storage not initialized")
    return _global_s3_storage
