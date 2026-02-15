"""Browser get text tool."""

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema
from aiwen.mcp_router import browser as browser_module


class BrowserGetTextTool(InnerTool):
    """Get inner text"""

    METADATA = ToolMetadata(
        name="browser_get_text",
        display_name="Browser Get Text",
        description="Extract the inner text content of an element identified by CSS selector",
        category="browser",
        tags=["browser", "extraction", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        selector: str = Field(description="CSS selector of the element")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_get_text(
            session_id=input_data.session_id,
            selector=input_data.selector,
        )
        return ToolOutputSchema(
            success=True,
            message=f"Retrieved text from element: {input_data.selector}",
            data=result,
        )
