"""
Browser automation tools exposed via MCP.

Provides a lightweight Playwright-backed session manager so agents can
drive a real browser with human-like actions (click/type/scroll).
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from fastmcp import FastMCP
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

browser_mcp = FastMCP("Aiwen Browser MCP")


@dataclass
class BrowserSession:
    browser: Browser
    context: BrowserContext
    page: Page


class BrowserManager:
    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._sessions: dict[str, BrowserSession] = {}

    async def _ensure_playwright(self) -> None:
        if self._playwright is None:
            self._playwright = await async_playwright().start()

    async def create_session(
        self,
        *,
        headless: bool = True,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        user_agent: str | None = None,
        slow_mo_ms: int = 0,
    ) -> str:
        await self._ensure_playwright()
        browser = await self._playwright.chromium.launch(
            headless=headless, slow_mo=slow_mo_ms
        )
        context = await browser.new_context(
            viewport={"width": viewport_width, "height": viewport_height},
            user_agent=user_agent,
        )
        page = await context.new_page()
        session_id = uuid4().hex
        self._sessions[session_id] = BrowserSession(
            browser=browser, context=context, page=page
        )
        return session_id

    def get_session(self, session_id: str) -> BrowserSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown browser session_id: {session_id}")
        return session

    async def close_session(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        await session.context.close()
        await session.browser.close()
        return True

    async def close_all(self) -> None:
        sessions = list(self._sessions.keys())
        for session_id in sessions:
            await self.close_session(session_id)
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


_manager = BrowserManager()


def _get_page(session_id: str) -> Page:
    return _manager.get_session(session_id).page


async def _browser_launch(
    *,
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    user_agent: str | None = None,
    slow_mo_ms: int = 0,
) -> dict:
    session_id = await _manager.create_session(
        headless=headless,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        user_agent=user_agent,
        slow_mo_ms=slow_mo_ms,
    )
    return {"session_id": session_id}


async def _browser_goto(
    *,
    session_id: str,
    url: str,
    wait_until: str = "load",
    timeout_ms: int = 30000,
) -> dict:
    page = _get_page(session_id)
    await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
    return {"status": "ok", "url": page.url}


async def _browser_click(
    *,
    session_id: str,
    selector: str,
    button: str = "left",
    delay_ms: int = 0,
) -> dict:
    page = _get_page(session_id)
    await page.click(selector, button=button, delay=delay_ms)
    return {"status": "ok"}


async def _browser_type(
    *,
    session_id: str,
    selector: str,
    text: str,
    delay_ms: int = 50,
    clear: bool = True,
) -> dict:
    page = _get_page(session_id)
    if clear:
        await page.fill(selector, "")
    await page.type(selector, text, delay=delay_ms)
    return {"status": "ok"}


async def _browser_press(
    *,
    session_id: str,
    selector: str,
    key: str,
) -> dict:
    page = _get_page(session_id)
    await page.press(selector, key)
    return {"status": "ok"}


async def _browser_wait_for(
    *,
    session_id: str,
    selector: str,
    state: str = "visible",
    timeout_ms: int = 30000,
) -> dict:
    page = _get_page(session_id)
    await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
    return {"status": "ok"}


async def _browser_sleep(*, session_id: str, duration_ms: int) -> dict:
    _get_page(session_id)
    await asyncio.sleep(duration_ms / 1000)
    return {"status": "ok"}


async def _browser_scroll(
    *, session_id: str, delta_y: int, delta_x: int = 0
) -> dict:
    page = _get_page(session_id)
    await page.mouse.wheel(delta_x, delta_y)
    return {"status": "ok"}


async def _browser_move_mouse(
    *, session_id: str, x: int, y: int, steps: int = 10
) -> dict:
    page = _get_page(session_id)
    await page.mouse.move(x, y, steps=steps)
    return {"status": "ok"}


async def _browser_screenshot(
    *, session_id: str, full_page: bool = False
) -> dict:
    page = _get_page(session_id)
    image_bytes = await page.screenshot(full_page=full_page)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {"screenshot_base64": encoded}


async def _browser_get_text(*, session_id: str, selector: str) -> dict:
    page = _get_page(session_id)
    text = await page.inner_text(selector)
    return {"text": text}


async def _browser_get_html(
    *, session_id: str, selector: str | None = None
) -> dict:
    page = _get_page(session_id)
    if selector:
        html = await page.inner_html(selector)
    else:
        html = await page.content()
    return {"html": html}


async def _browser_close(*, session_id: str) -> dict:
    closed = await _manager.close_session(session_id)
    return {"status": "ok" if closed else "not_found"}


browser_launch = browser_mcp.tool(
    _browser_launch, name="browser_launch", description="Launch a browser session."
)
browser_goto = browser_mcp.tool(
    _browser_goto, name="browser_goto", description="Navigate to a URL."
)
browser_click = browser_mcp.tool(
    _browser_click, name="browser_click", description="Click an element."
)
browser_type = browser_mcp.tool(
    _browser_type, name="browser_type", description="Type text into an element."
)
browser_press = browser_mcp.tool(
    _browser_press, name="browser_press", description="Press a key on an element."
)
browser_wait_for = browser_mcp.tool(
    _browser_wait_for,
    name="browser_wait_for",
    description="Wait for an element state.",
)
browser_sleep = browser_mcp.tool(
    _browser_sleep, name="browser_sleep", description="Pause for a duration in ms."
)
browser_scroll = browser_mcp.tool(
    _browser_scroll, name="browser_scroll", description="Scroll the page."
)
browser_move_mouse = browser_mcp.tool(
    _browser_move_mouse, name="browser_move_mouse", description="Move mouse cursor."
)
browser_screenshot = browser_mcp.tool(
    _browser_screenshot, name="browser_screenshot", description="Take a screenshot."
)
browser_get_text = browser_mcp.tool(
    _browser_get_text, name="browser_get_text", description="Get inner text."
)
browser_get_html = browser_mcp.tool(
    _browser_get_html, name="browser_get_html", description="Get HTML content."
)
browser_close = browser_mcp.tool(
    _browser_close, name="browser_close", description="Close a browser session."
)


async def shutdown_browser_sessions() -> None:
    await _manager.close_all()
