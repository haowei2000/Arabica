"""HTTP request tool."""

import logging
from typing import Any

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema

logger = logging.getLogger(__name__)


class HttpRequestInnerTool(InnerTool):
    """Make HTTP API calls

    This is the built-in execution backend for user-defined HTTP tools.
    ExternalTool instances delegate to this tool to make HTTP requests.
    """

    METADATA = ToolMetadata(
        name="http_request",
        display_name="HTTP Request",
        description="Make HTTP API calls with configurable method, headers, and body",
        category="execution",
        tags=["http", "api", "request", "inner"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        url: str = Field(description="Target URL")
        method: str = Field(
            default="POST", description="HTTP method (GET, POST, PUT, DELETE, PATCH)"
        )
        headers: dict[str, str] = Field(
            default_factory=dict, description="Request headers"
        )
        body: dict = Field(default_factory=dict, description="Request body (JSON)")
        timeout: int = Field(
            default=30, ge=1, le=300, description="Request timeout in seconds"
        )
        verify_ssl: bool = Field(
            default=True, description="Whether to verify SSL certificates"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Execute HTTP request"""
        import httpx

        try:
            async with httpx.AsyncClient(verify=input_data.verify_ssl) as client:
                request_kwargs: dict[str, Any] = {
                    "method": input_data.method.upper(),
                    "url": input_data.url,
                    "headers": input_data.headers,
                    "timeout": input_data.timeout,
                }

                if (
                    input_data.method.upper() in ("POST", "PUT", "PATCH")
                    and input_data.body
                ):
                    request_kwargs["json"] = input_data.body
                elif input_data.body:
                    request_kwargs["params"] = input_data.body

                response = await client.request(**request_kwargs)

                try:
                    response_data = response.json()
                except Exception:
                    response_data = {"text": response.text}

                return ToolOutputSchema(
                    success=response.is_success,
                    message=f"HTTP {input_data.method.upper()} {input_data.url} -> {response.status_code}",
                    data={
                        "status_code": response.status_code,
                        "headers": dict(response.headers),
                        "body": response_data,
                    },
                )

        except httpx.TimeoutException:
            return ToolOutputSchema(
                success=False,
                error=f"HTTP request timed out after {input_data.timeout}s",
            )
        except Exception as e:
            logger.error(f"HTTP request error: {e}", exc_info=True)
            return ToolOutputSchema(
                success=False,
                error=f"HTTP request failed: {e!s}",
            )
