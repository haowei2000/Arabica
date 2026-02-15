"""Browser press key tool."""

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema
from aiwen.mcp_router import browser as browser_module


class BrowserPressTool(InnerTool):
    """Press a key on an element"""

    METADATA = ToolMetadata(
        name="browser_press",
        display_name="Browser Press Key",
        description="Press a keyboard key on a focused element (e.g., 'Enter', 'Tab', 'Escape')",
        category="browser",
        tags=["browser", "keyboard", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        selector: str = Field(description="CSS selector of the element")
        key: str = Field(description="Key to press (e.g., 'Enter', 'Tab', 'ArrowDown')")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_press(
            session_id=input_data.session_id,
            selector=input_data.selector,
            key=input_data.key,
        )
        return ToolOutputSchema(
            success=True,
            message=f"Pressed key '{input_data.key}' on element: {input_data.selector}",
            data=result,
        )
