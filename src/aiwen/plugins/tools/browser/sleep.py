"""Browser sleep tool."""

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema
from aiwen.mcp_router import browser as browser_module


class BrowserSleepTool(InnerTool):
    """Pause for a duration in ms"""

    METADATA = ToolMetadata(
        name="browser_sleep",
        display_name="Browser Sleep",
        description="Pause browser automation for a specified duration",
        category="browser",
        tags=["browser", "wait", "playwright"],
        timeout=120,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        duration_ms: int = Field(description="Duration to sleep in milliseconds")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_sleep(
            session_id=input_data.session_id,
            duration_ms=input_data.duration_ms,
        )
        return ToolOutputSchema(
            success=True,
            message=f"Paused for {input_data.duration_ms}ms",
            data=result,
        )
