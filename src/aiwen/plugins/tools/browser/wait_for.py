"""Browser wait for element tool."""

from aiwen.mcp_router import browser as browser_module
from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class BrowserWaitForTool(InnerTool):
    """Wait for an element state"""

    METADATA = ToolMetadata(
        name="browser_wait_for",
        display_name="Browser Wait For Element",
        description="Wait for an element to reach a specific state (visible, hidden, attached, detached)",
        category="browser",
        tags=["browser", "wait", "playwright"],
        timeout=60,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        selector: str = Field(description="CSS selector of the element")
        state: str = Field(
            default="visible",
            description="State to wait for: 'visible', 'hidden', 'attached', or 'detached'",
        )
        timeout_ms: int = Field(default=30000, description="Wait timeout in milliseconds")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_wait_for(
            session_id=input_data.session_id,
            selector=input_data.selector,
            state=input_data.state,
            timeout_ms=input_data.timeout_ms,
        )
        return ToolOutputSchema(
            success=True,
            message=f"Element {input_data.selector} reached state: {input_data.state}",
            data=result,
        )
