"""Browser click tool."""

from aiwen.mcp_router import browser as browser_module
from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class BrowserClickTool(InnerTool):
    """Click an element"""

    METADATA = ToolMetadata(
        name="browser_click",
        display_name="Browser Click",
        description="Click an element identified by CSS selector",
        category="browser",
        tags=["browser", "interaction", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        selector: str = Field(description="CSS selector of the element to click")
        button: str = Field(
            default="left", description="Mouse button: 'left', 'right', or 'middle'"
        )
        delay_ms: int = Field(
            default=0, description="Delay between mousedown and mouseup in milliseconds"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_click(
            session_id=input_data.session_id,
            selector=input_data.selector,
            button=input_data.button,
            delay_ms=input_data.delay_ms,
        )
        return ToolOutputSchema(
            success=True,
            message=f"Clicked element: {input_data.selector}",
            data=result,
        )
