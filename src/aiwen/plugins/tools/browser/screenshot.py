"""Browser screenshot tool."""

from aiwen.mcp_router import browser as browser_module
from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class BrowserScreenshotTool(InnerTool):
    """Take a screenshot and return base64 bytes"""

    METADATA = ToolMetadata(
        name="browser_screenshot",
        display_name="Browser Screenshot",
        description="Capture a screenshot of the current page as base64-encoded image data",
        category="browser",
        tags=["browser", "screenshot", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        full_page: bool = Field(
            default=False,
            description="Capture full scrollable page instead of just viewport",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_screenshot(
            session_id=input_data.session_id,
            full_page=input_data.full_page,
        )
        return ToolOutputSchema(
            success=True,
            message="Screenshot captured successfully",
            data=result,
        )
