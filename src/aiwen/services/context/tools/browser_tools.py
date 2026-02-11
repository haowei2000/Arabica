"""
Browser automation tools using BaseTool system.

These wrap the Playwright-backed browser manager used by MCP so
agents can call tools directly in-process.
"""

from pydantic import Field

from aiwen.mcp_router import browser as browser_module
from aiwen.registries.core import register_tool
from aiwen.services.context.tools.base_tool import (
    InnerTool,
    ToolExecutionMode,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


@register_tool
class BrowserLaunchTool(InnerTool):
    """Launch a browser session and return a session_id"""

    METADATA = ToolMetadata(
        name="browser_launch",
        display_name="Browser Launch",
        description="Launch a browser session with configurable options (headless mode, viewport, user agent)",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="browser",
        tags=["browser", "automation", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        headless: bool = Field(default=True, description="Run browser in headless mode")
        viewport_width: int = Field(
            default=1280, description="Viewport width in pixels"
        )
        viewport_height: int = Field(
            default=720, description="Viewport height in pixels"
        )
        user_agent: str | None = Field(
            default=None, description="Custom user agent string"
        )
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

@register_tool
class BrowserGotoTool(InnerTool):
    """Navigate to a URL"""

    METADATA = ToolMetadata(
        name="browser_goto",
        display_name="Browser Navigate",
        description="Navigate to a specified URL with optional wait conditions",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="browser",
        tags=["browser", "navigation", "playwright"],
        timeout=60,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        url: str = Field(description="URL to navigate to")
        wait_until: str = Field(
            default="load",
            description="When to consider navigation complete: 'load', 'domcontentloaded', 'networkidle', or 'commit'",
        )
        timeout_ms: int = Field(
            default=30000, description="Navigation timeout in milliseconds"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_goto(
            session_id=input_data.session_id,
            url=input_data.url,
            wait_until=input_data.wait_until,
            timeout_ms=input_data.timeout_ms,
        )
        return ToolOutputSchema(
            success=True,
            message=f"Navigated to {input_data.url}",
            data=result,
        )

@register_tool
class BrowserClickTool(InnerTool):
    """Click an element"""

    METADATA = ToolMetadata(
        name="browser_click",
        display_name="Browser Click",
        description="Click an element identified by CSS selector",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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

@register_tool
class BrowserTypeTool(InnerTool):
    """Type text into an element"""

    METADATA = ToolMetadata(
        name="browser_type",
        display_name="Browser Type",
        description="Type text into an input field or textarea identified by CSS selector",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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
        clear: bool = Field(
            default=True, description="Clear existing text before typing"
        )

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

@register_tool
class BrowserPressTool(InnerTool):
    """Press a key on an element"""

    METADATA = ToolMetadata(
        name="browser_press",
        display_name="Browser Press Key",
        description="Press a keyboard key on a focused element (e.g., 'Enter', 'Tab', 'Escape')",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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

@register_tool
class BrowserWaitForTool(InnerTool):
    """Wait for an element state"""

    METADATA = ToolMetadata(
        name="browser_wait_for",
        display_name="Browser Wait For Element",
        description="Wait for an element to reach a specific state (visible, hidden, attached, detached)",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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
        timeout_ms: int = Field(
            default=30000, description="Wait timeout in milliseconds"
        )

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

@register_tool
class BrowserSleepTool(InnerTool):
    """Pause for a duration in ms"""

    METADATA = ToolMetadata(
        name="browser_sleep",
        display_name="Browser Sleep",
        description="Pause browser automation for a specified duration",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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

@register_tool
class BrowserScrollTool(InnerTool):
    """Scroll the page"""

    METADATA = ToolMetadata(
        name="browser_scroll",
        display_name="Browser Scroll",
        description="Scroll the page by specified pixel amounts (vertical and horizontal)",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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

@register_tool
class BrowserMoveMouseTool(InnerTool):
    """Move mouse cursor"""

    METADATA = ToolMetadata(
        name="browser_move_mouse",
        display_name="Browser Move Mouse",
        description="Move the mouse cursor to specific coordinates with smooth steps",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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

@register_tool
class BrowserScreenshotTool(InnerTool):
    """Take a screenshot and return base64 bytes"""

    METADATA = ToolMetadata(
        name="browser_screenshot",
        display_name="Browser Screenshot",
        description="Capture a screenshot of the current page as base64-encoded image data",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="browser",
        tags=["browser", "screenshot", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID")
        full_page: bool = Field(
            default=False,
            description="Capture full scrollable page instead of just viewport",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_screenshot(
            session_id=input_data.session_id,
            full_page=input_data.full_page,
        )
        return ToolOutputSchema(
            success=True,
            message="Screenshot captured successfully",
            data=result,
        )

@register_tool
class BrowserGetTextTool(InnerTool):
    """Get inner text"""

    METADATA = ToolMetadata(
        name="browser_get_text",
        display_name="Browser Get Text",
        description="Extract the inner text content of an element identified by CSS selector",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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

@register_tool
class BrowserGetHtmlTool(InnerTool):
    """Get HTML content"""

    METADATA = ToolMetadata(
        name="browser_get_html",
        display_name="Browser Get HTML",
        description="Extract HTML content of an element or the entire page",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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

@register_tool
class BrowserCloseTool(InnerTool):
    """Close a browser session"""

    METADATA = ToolMetadata(
        name="browser_close",
        display_name="Browser Close",
        description="Close a browser session and release resources",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="browser",
        tags=["browser", "cleanup", "playwright"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        session_id: str = Field(description="Browser session ID to close")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        result = await browser_module._browser_close(
            session_id=input_data.session_id,
        )
        return ToolOutputSchema(
            success=True,
            message="Browser session closed successfully",
            data=result,
        )


# Export tool collections
BROWSER_TOOLS = [
    BrowserLaunchTool,
    BrowserGotoTool,
    BrowserClickTool,
    BrowserTypeTool,
    BrowserPressTool,
    BrowserWaitForTool,
    BrowserSleepTool,
    BrowserScrollTool,
    BrowserMoveMouseTool,
    BrowserScreenshotTool,
    BrowserGetTextTool,
    BrowserGetHtmlTool,
    BrowserCloseTool,
]

__all__ = [
    "BROWSER_TOOLS",
    "BrowserClickTool",
    "BrowserCloseTool",
    "BrowserGetHtmlTool",
    "BrowserGetTextTool",
    "BrowserGotoTool",
    "BrowserLaunchTool",
    "BrowserMoveMouseTool",
    "BrowserPressTool",
    "BrowserScreenshotTool",
    "BrowserScrollTool",
    "BrowserSleepTool",
    "BrowserTypeTool",
    "BrowserWaitForTool",
]
