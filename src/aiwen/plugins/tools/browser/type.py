"""Browser type tool."""

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema
from aiwen.mcp_router import browser as browser_module


class BrowserTypeTool(InnerTool):
    """Type text into an element"""

    METADATA = ToolMetadata(
        name="browser_type",
        display_name="Browser Type",
        description="Type text into an input field or textarea identified by CSS selector",
        category="browser",
        tags=["browser", "input", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        selector: str = Field(description="CSS selector of the input element")
        text: str = Field(description="Text to type")
        delay_ms: int = Field(
            default=50, description="Delay between key presses in milliseconds"
        )
        clear: bool = Field(default=True, description="Clear existing text before typing")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_type(
            session_id=input_data.session_id,
            selector=input_data.selector,
            text=input_data.text,
            delay_ms=input_data.delay_ms,
            clear=input_data.clear,
        )
        return ToolOutputSchema(
            success=True,
            message=f"Typed text into element: {input_data.selector}",
            data=result,
        )
