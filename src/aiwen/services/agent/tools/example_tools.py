"""
Example tools demonstrating different execution modes.

This module provides example implementations for:
- Sandbox tools (Docker-isolated execution)
- Client tools (browser-side execution)
- Server tools (default in-process execution)
"""

from langchain_core.tools import tool

from aiwen.services.agent.tools.execution_mode import (
    client_tool,
    sandbox_tool,
    server_tool,
)

# ═══════════════════════════════════════════════════════════════════════════════
# SANDBOX TOOLS - Execute in isolated Docker containers
# ═══════════════════════════════════════════════════════════════════════════════


@tool("execute_python")
@sandbox_tool(image="python:3.12-slim", memory="256m", timeout=60, network=False)
async def execute_python(code: str) -> dict:
    """
    Execute Python code safely in an isolated sandbox.

    The code runs in a Docker container with:
    - No network access
    - Limited memory (256MB)
    - Limited CPU (50%)
    - 60 second timeout

    Args:
        code: Python code to execute

    Returns:
        dict with 'output' (stdout) and 'error' (stderr if any)
    """
    # Note: Actual execution is handled by SandboxExecutor
    # This function body is not executed directly
    pass


@tool("execute_shell")
@sandbox_tool(image="alpine:latest", memory="128m", timeout=30, network=False)
async def execute_shell(command: str) -> dict:
    """
    Execute a shell command safely in an isolated sandbox.

    The command runs in an Alpine Linux container with strict limits.

    Args:
        command: Shell command to execute

    Returns:
        dict with 'output' (stdout) and 'exit_code'
    """
    pass


@tool("run_javascript")
@sandbox_tool(image="node:20-slim", memory="256m", timeout=30)
async def run_javascript(code: str) -> dict:
    """
    Execute JavaScript/Node.js code in a sandbox.

    Args:
        code: JavaScript code to execute

    Returns:
        dict with execution result
    """
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# CLIENT TOOLS - Execute in the user's browser
# ═══════════════════════════════════════════════════════════════════════════════


@tool("select_file")
@client_tool(handler="filePicker", timeout=120)
async def select_file(
    allowed_extensions: list[str] | None = None,
    multiple: bool = False,
) -> dict:
    """
    Open a file picker dialog in the user's browser.

    This tool triggers a native file picker in the browser and returns
    information about the selected file(s).

    Args:
        allowed_extensions: List of allowed file extensions (e.g., [".pdf", ".txt"])
        multiple: Whether to allow selecting multiple files

    Returns:
        dict with file information:
        - name: File name
        - size: File size in bytes
        - type: MIME type
        - content: Base64-encoded file content (for small files)
    """
    pass


@tool("capture_screenshot")
@client_tool(handler="screenshotCapture", timeout=60)
async def capture_screenshot(
    include_cursor: bool = False,
    region: str | None = None,
) -> dict:
    """
    Capture a screenshot from the user's screen.

    This requires screen capture permission in the browser.

    Args:
        include_cursor: Whether to include the cursor in the screenshot
        region: Optional region to capture ("full", "window", "selection")

    Returns:
        dict with:
        - image: Base64-encoded PNG image
        - width: Image width
        - height: Image height
    """
    pass


@tool("get_clipboard")
@client_tool(handler="clipboardRead", timeout=30)
async def get_clipboard() -> dict:
    """
    Read the current clipboard content.

    This requires clipboard permission in the browser.

    Returns:
        dict with:
        - text: Text content (if available)
        - html: HTML content (if available)
        - has_image: Whether clipboard contains an image
    """
    pass


@tool("get_geolocation")
@client_tool(handler="geolocation", timeout=30)
async def get_geolocation(high_accuracy: bool = False) -> dict:
    """
    Get the user's current geolocation.

    This requires location permission in the browser.

    Args:
        high_accuracy: Whether to request high accuracy (may take longer)

    Returns:
        dict with:
        - latitude: Latitude in decimal degrees
        - longitude: Longitude in decimal degrees
        - accuracy: Accuracy in meters
    """
    pass


@tool("camera_capture")
@client_tool(handler="cameraCapture", timeout=60, config={"facing_mode": "user"})
async def camera_capture(
    facing_mode: str = "user",
    width: int = 640,
    height: int = 480,
) -> dict:
    """
    Capture a photo from the user's camera.

    This requires camera permission in the browser.

    Args:
        facing_mode: "user" for front camera, "environment" for back camera
        width: Desired image width
        height: Desired image height

    Returns:
        dict with:
        - image: Base64-encoded JPEG image
        - width: Actual image width
        - height: Actual image height
    """
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# SERVER TOOLS - Execute in-process on the server (explicit marking)
# ═══════════════════════════════════════════════════════════════════════════════


@tool("database_query")
@server_tool(timeout=60)
async def database_query(query: str, params: dict | None = None) -> dict:
    """
    Execute a database query (server-side only).

    This is explicitly marked as a server tool for clarity.

    Args:
        query: SQL query to execute
        params: Query parameters

    Returns:
        dict with query results
    """
    # This would be implemented to actually query the database
    return {"rows": [], "row_count": 0}


@tool("send_email")
@server_tool(timeout=30)
async def send_email(
    to: str,
    subject: str,
    body: str,
    html: bool = False,
) -> dict:
    """
    Send an email (server-side only).

    Args:
        to: Recipient email address
        subject: Email subject
        body: Email body
        html: Whether body is HTML

    Returns:
        dict with send status
    """
    # This would be implemented to actually send email
    return {"sent": True, "message_id": "mock-id"}


# ═══════════════════════════════════════════════════════════════════════════════
# Tool collections for easy import
# ═══════════════════════════════════════════════════════════════════════════════

SANDBOX_TOOLS = [
    execute_python,
    execute_shell,
    run_javascript,
]

CLIENT_TOOLS = [
    select_file,
    capture_screenshot,
    get_clipboard,
    get_geolocation,
    camera_capture,
]

SERVER_TOOLS = [
    database_query,
    send_email,
]

ALL_EXAMPLE_TOOLS = SANDBOX_TOOLS + CLIENT_TOOLS + SERVER_TOOLS
