"""Browser scroll tool."""

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema
from aiwen.mcp_router import browser as browser_module


class BrowserScrollTool(InnerTool):
    """Scroll the page"""

    METADATA = ToolMetadata(
        name="browser_scroll",
        display_name="Browser Scroll",
        description="Scroll the page by specified pixel amounts (vertical and horizontal)",
        category="browser",
        tags=["browser", "scroll", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        delta_y: int = Field(
            description="Vertical scroll amount in pixels (positive = down, negative = up)"
        )
        delta_x: int = Field(
            default=0,
            description="Horizontal scroll amount in pixels (positive = right, negative = left)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_scroll(
            session_id=input_data.session_id,
            delta_y=input_data.delta_y,
            delta_x=input_data.delta_x,
        )
        return ToolOutputSchema(
            success=True,
            message=f"Scrolled by ({input_data.delta_x}, {input_data.delta_y})",
            data=result,
        )
