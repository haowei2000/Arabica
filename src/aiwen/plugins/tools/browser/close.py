"""Browser close tool."""

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema
from aiwen.mcp_router import browser as browser_module


class BrowserCloseTool(InnerTool):
    """Close a browser session"""

    METADATA = ToolMetadata(
        name="browser_close",
        display_name="Browser Close",
        description="Close a browser session and release resources",
        category="browser",
        tags=["browser", "cleanup", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID to close")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_close(
            session_id=input_data.session_id,
        )
        return ToolOutputSchema(
            success=True,
            message="Browser session closed successfully",
            data=result,
        )
