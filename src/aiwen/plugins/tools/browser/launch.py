"""Browser launch tool."""

from aiwen.mcp_router import browser as browser_module
from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class BrowserLaunchTool(InnerTool):
    """Launch a browser session and return a session_id"""

    METADATA = ToolMetadata(
        name="browser_launch",
        display_name="Browser Launch",
        description="Launch a browser session with configurable options (headless mode, viewport, user agent)",
        category="browser",
        tags=["browser", "automation", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        headless: bool = Field(default=True, description="Run browser in headless mode")
        viewport_width: int = Field(default=1280, description="Viewport width in pixels")
        viewport_height: int = Field(default=720, description="Viewport height in pixels")
        user_agent: str | None = Field(default=None, description="Custom user agent string")
        slow_mo_ms: int = Field(
            default=0, description="Slow down operations by specified milliseconds"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_launch(
            headless=input_data.headless,
            viewport_width=input_data.viewport_width,
            viewport_height=input_data.viewport_height,
            user_agent=input_data.user_agent,
            slow_mo_ms=input_data.slow_mo_ms,
        )
        return ToolOutputSchema(
            success=True,
            message="Browser session launched successfully",
            data=result,
        )
