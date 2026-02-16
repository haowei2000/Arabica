"""Browser get HTML tool."""

from aiwen.mcp_router import browser as browser_module
from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class BrowserGetHtmlTool(InnerTool):
    """Get HTML content"""

    METADATA = ToolMetadata(
        name="browser_get_html",
        display_name="Browser Get HTML",
        description="Extract HTML content of an element or the entire page",
        category="browser",
        tags=["browser", "extraction", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        selector: str | None = Field(
            default=None, description="CSS selector of element (None = entire page)"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_get_html(
            session_id=input_data.session_id,
            selector=input_data.selector,
        )
        return ToolOutputSchema(
            success=True,
            message="Retrieved HTML content",
            data=result,
        )
