"""Cache set tool."""

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class CacheSetTool(InnerTool):
    """Set a value in the cache"""

    METADATA = ToolMetadata(
        name="cache_set",
        display_name="Cache Set",
        description="Set a value in the Redis cache with TTL",
        category="utility",
        tags=["cache", "redis", "storage"],
        timeout=5,
    )

    class InputSchema(ToolInputSchema):
        key: str = Field(description="Cache key")
        value: str = Field(description="Value to cache (string)")
        namespace: str = Field(default="default", description="Cache namespace")
        ttl_seconds: int = Field(
            default=3600, ge=1, le=86400 * 7, description="Time-to-live in seconds"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from structure.middleware.cache_middleware import get_redis_client

        redis = get_redis_client(is_async=True)
        full_key = f"{input_data.namespace}:{input_data.key}"

        await redis.setex(full_key, input_data.ttl_seconds, input_data.value)

        return ToolOutputSchema(
            success=True,
            message=f"Cached value for key '{input_data.key}' with TTL {input_data.ttl_seconds}s",
            data={
                "key": input_data.key,
                "namespace": input_data.namespace,
                "ttl_seconds": input_data.ttl_seconds,
            },
        )
