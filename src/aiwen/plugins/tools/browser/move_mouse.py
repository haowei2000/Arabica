"""Browser move mouse tool."""

from aiwen.mcp_router import browser as browser_module
from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class BrowserMoveMouseTool(InnerTool):
    """Move mouse cursor"""

    METADATA = ToolMetadata(
        name="browser_move_mouse",
        display_name="Browser Move Mouse",
        description="Move the mouse cursor to specific coordinates with smooth steps",
        category="browser",
        tags=["browser", "mouse", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        x: int = Field(description="X coordinate in pixels")
        y: int = Field(description="Y coordinate in pixels")
        steps: int = Field(
            default=10, description="Number of intermediate steps for smooth movement"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_move_mouse(
            session_id=input_data.session_id,
            x=input_data.x,
            y=input_data.y,
            steps=input_data.steps,
        )
        return ToolOutputSchema(
            success=True,
            message=f"Moved mouse to ({input_data.x}, {input_data.y})",
            data=result,
        )
