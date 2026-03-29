"""Cache get tool."""

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class CacheGetTool(InnerTool):
    """Get a value from the cache"""

    METADATA = ToolMetadata(
        name="cache_get",
        display_name="Cache Get",
        description="Get a value from the Redis cache",
        category="utility",
        tags=["cache", "redis", "storage"],
        timeout=5,
    )

    class InputSchema(ToolInputSchema):
        key: str = Field(description="Cache key")
        namespace: str = Field(
            default="default", description="Cache namespace for isolation"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from structure.middleware.cache_middleware import get_redis_client

        redis = get_redis_client(is_async=True)
        full_key = f"{input_data.namespace}:{input_data.key}"

        value = await redis.get(full_key)
        ttl = await redis.ttl(full_key) if value else -1

        return ToolOutputSchema(
            success=True,
            message=f"Retrieved value for key '{input_data.key}'"
            if value
            else f"Key '{input_data.key}' not found",
            data={
                "value": value.decode("utf-8") if value else None,
                "found": value is not None,
                "ttl": ttl if ttl > 0 else None,
                "key": input_data.key,
                "namespace": input_data.namespace,
            },
        )
