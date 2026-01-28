"""
LangChain tools for browser automation.

These wrap the Playwright-backed browser manager used by MCP so
agents can call tools directly in-process.
"""

from langchain_core.tools import tool

from aiwen.mcp_router import browser as browser_module


@tool("browser_launch")
async def browser_launch(
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    user_agent: str | None = None,
    slow_mo_ms: int = 0,
) -> dict:
    """Launch a browser session and return a session_id."""
    return await browser_module._browser_launch(
        headless=headless,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        user_agent=user_agent,
        slow_mo_ms=slow_mo_ms,
    )


@tool("browser_goto")
async def browser_goto(
    session_id: str,
    url: str,
    wait_until: str = "load",
    timeout_ms: int = 30000,
) -> dict:
    """Navigate to a URL."""
    return await browser_module._browser_goto(
        session_id=session_id,
        url=url,
        wait_until=wait_until,
        timeout_ms=timeout_ms,
    )


@tool("browser_click")
async def browser_click(
    session_id: str, selector: str, button: str = "left", delay_ms: int = 0
) -> dict:
    """Click an element."""
    return await browser_module._browser_click(
        session_id=session_id,
        selector=selector,
        button=button,
        delay_ms=delay_ms,
    )


@tool("browser_type")
async def browser_type(
    session_id: str,
    selector: str,
    text: str,
    delay_ms: int = 50,
    clear: bool = True,
) -> dict:
    """Type text into an element."""
    return await browser_module._browser_type(
        session_id=session_id,
        selector=selector,
        text=text,
        delay_ms=delay_ms,
        clear=clear,
    )


@tool("browser_press")
async def browser_press(session_id: str, selector: str, key: str) -> dict:
    """Press a key on an element."""
    return await browser_module._browser_press(
        session_id=session_id, selector=selector, key=key
    )


@tool("browser_wait_for")
async def browser_wait_for(
    session_id: str, selector: str, state: str = "visible", timeout_ms: int = 30000
) -> dict:
    """Wait for an element state."""
    return await browser_module._browser_wait_for(
        session_id=session_id,
        selector=selector,
        state=state,
        timeout_ms=timeout_ms,
    )


@tool("browser_sleep")
async def browser_sleep(session_id: str, duration_ms: int) -> dict:
    """Pause for a duration in ms."""
    return await browser_module._browser_sleep(
        session_id=session_id, duration_ms=duration_ms
    )


@tool("browser_scroll")
async def browser_scroll(session_id: str, delta_y: int, delta_x: int = 0) -> dict:
    """Scroll the page."""
    return await browser_module._browser_scroll(
        session_id=session_id, delta_y=delta_y, delta_x=delta_x
    )


@tool("browser_move_mouse")
async def browser_move_mouse(
    session_id: str, x: int, y: int, steps: int = 10
) -> dict:
    """Move mouse cursor."""
    return await browser_module._browser_move_mouse(
        session_id=session_id, x=x, y=y, steps=steps
    )


@tool("browser_screenshot")
async def browser_screenshot(session_id: str, full_page: bool = False) -> dict:
    """Take a screenshot and return base64 bytes."""
    return await browser_module._browser_screenshot(
        session_id=session_id, full_page=full_page
    )


@tool("browser_get_text")
async def browser_get_text(session_id: str, selector: str) -> dict:
    """Get inner text."""
    return await browser_module._browser_get_text(
        session_id=session_id, selector=selector
    )


@tool("browser_get_html")
async def browser_get_html(session_id: str, selector: str | None = None) -> dict:
    """Get HTML content."""
    return await browser_module._browser_get_html(
        session_id=session_id, selector=selector
    )


@tool("browser_close")
async def browser_close(session_id: str) -> dict:
    """Close a browser session."""
    return await browser_module._browser_close(session_id=session_id)


BROWSER_TOOLS = [
    browser_launch,
    browser_goto,
    browser_click,
    browser_type,
    browser_press,
    browser_wait_for,
    browser_sleep,
    browser_scroll,
    browser_move_mouse,
    browser_screenshot,
    browser_get_text,
    browser_get_html,
    browser_close,
]
