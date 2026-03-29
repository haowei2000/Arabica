#!/usr/bin/env python3
"""
Initialize built-in tools by importing all inner tools from the structure-mcp server.

Runs once on startup. Probes the MCP server, then imports every tool it
exposes into the tool library as a public MCP tool so all agents can use them.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = os.getenv("API_URL", "http://app:8000").rstrip("/")
MCP_URL = os.getenv("MCP_URL", "http://mcp-server:9000").rstrip("/")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "123456")
WAIT_TIMEOUT = int(os.getenv("WAIT_TIMEOUT", "120"))
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "sse")
MCP_SSE_PATH = os.getenv("MCP_SSE_PATH", "/mcp")


def _request(url: str, data=None, headers: dict | None = None, method: str | None = None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method or ("POST" if body else "GET"),
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def wait_for_http(url: str, label: str, timeout: int = WAIT_TIMEOUT):
    """Poll url until the server responds (any HTTP status) or timeout."""
    deadline = time.monotonic() + timeout
    interval = 3
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=5)
            print(f"[init] {label} is ready")
            return
        except urllib.error.HTTPError:
            # Server responded with 4xx/5xx — it is up, just wrong path
            print(f"[init] {label} is ready")
            return
        except Exception:
            print(f"[init] Waiting for {label} at {url} ...")
            time.sleep(interval)
    print(f"[init] ERROR: {label} did not become ready within {timeout}s", file=sys.stderr)
    sys.exit(1)


def login() -> str:
    """Login as admin and return the access token."""
    form = urllib.parse.urlencode(
        {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    ).encode()
    req = urllib.request.Request(
        f"{API_URL}/api/auth/login",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    token = data.get("access_token")
    if not token:
        print("[init] ERROR: Login failed — no access_token in response", file=sys.stderr)
        sys.exit(1)
    print(f"[init] Logged in as '{ADMIN_USERNAME}'")
    return token


def probe_mcp(token: str) -> list[str]:
    """Probe the MCP server and return all tool names."""
    mcp_endpoint = f"{MCP_URL}{MCP_SSE_PATH}"
    payload = {"transport": MCP_TRANSPORT, "url": mcp_endpoint}
    try:
        result = _request(
            f"{API_URL}/api/tools/probe-mcp",
            data=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
    except urllib.error.HTTPError as e:
        print(f"[init] ERROR: probe-mcp failed: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

    if not result.get("success"):
        print(f"[init] ERROR: MCP probe failed: {result.get('error')}", file=sys.stderr)
        sys.exit(1)

    names = [t["name"] for t in result.get("tools", [])]
    print(f"[init] Found {len(names)} tools on MCP server: {', '.join(names)}")
    return names


def import_tools(token: str, tool_names: list[str]):
    """Import all tools from MCP server as public tools."""
    if not tool_names:
        print("[init] No tools to import.")
        return

    mcp_endpoint = f"{MCP_URL}{MCP_SSE_PATH}"
    payload = {
        "transport": MCP_TRANSPORT,
        "url": mcp_endpoint,
        "tool_names": tool_names,
        "is_public": True,
    }
    try:
        result = _request(
            f"{API_URL}/api/tools/import-from-mcp",
            data=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
    except urllib.error.HTTPError as e:
        print(f"[init] ERROR: import-from-mcp failed: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

    imported = result.get("imported", [])
    skipped = result.get("skipped", [])
    failed = result.get("failed", [])

    print(f"[init] Import complete — imported: {len(imported)}, skipped: {len(skipped)}, failed: {len(failed)}")
    if imported:
        print(f"[init]   Imported: {', '.join(imported)}")
    if skipped:
        print(f"[init]   Skipped (already exist): {', '.join(skipped)}")
    if failed:
        print(f"[init]   Failed: {', '.join(failed)}", file=sys.stderr)


def main():
    print("[init] Starting built-in tool initialization...")

    wait_for_http(f"{API_URL}/health", "API server")
    wait_for_http(f"{MCP_URL}/health", "MCP server")

    token = login()
    tool_names = probe_mcp(token)
    import_tools(token, tool_names)

    print("[init] Done.")


if __name__ == "__main__":
    main()
