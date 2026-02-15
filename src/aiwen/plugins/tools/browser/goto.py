"""Browser navigation tool."""

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema
from aiwen.mcp_router import browser as browser_module


class BrowserGotoTool(InnerTool):
    """Navigate to a URL"""

    METADATA = ToolMetadata(
        name="browser_goto",
        display_name="Browser Navigate",
        description="Navigate to a specified URL with optional wait conditions",
        category="browser",
        tags=["browser", "navigation", "playwright"],
        timeout=60,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        url: str = Field(description="URL to navigate to")
        wait_until: str = Field(
            default="load",
            description="When to consider navigation complete: 'load', 'domcontentloaded', 'networkidle', or 'commit'",
        )
        timeout_ms: int = Field(default=30000, description="Navigation timeout in milliseconds")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_goto(
            session_id=input_data.session_id,
            url=input_data.url,
            wait_until=input_data.wait_until,
            timeout_ms=input_data.timeout_ms,
        )
        return ToolOutputSchema(
            success=True,
            message=f"Navigated to {input_data.url}",
            data=result,
        )
