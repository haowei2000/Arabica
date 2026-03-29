# Tool Execution Mode Architecture

> A flexible system for running agent tools in different environments: Server, Sandbox (Docker), and Client (Browser).

## Overview

The Tool Execution Mode system enables agents to run tools in the most appropriate environment based on security requirements, resource needs, and user interaction patterns.

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent Executor                           │
│                              │                                  │
│                              ▼                                  │
│                  ┌───────────────────────┐                     │
│                  │   ExecutionRouter     │                     │
│                  │   (checks metadata)   │                     │
│                  └───────────┬───────────┘                     │
│                              │                                  │
│            ┌─────────────────┼─────────────────┐               │
│            ▼                 ▼                 ▼               │
│     ┌────────────┐    ┌────────────┐    ┌────────────┐        │
│     │   Server   │    │  Sandbox   │    │   Client   │        │
│     │  (default) │    │  (Docker)  │    │ (Browser)  │        │
│     └────────────┘    └────────────┘    └────────────┘        │
│            │                 │                 │               │
│            ▼                 ▼                 ▼               │
│       In-process        Isolated          User's              │
│       execution        container          browser             │
└─────────────────────────────────────────────────────────────────┘
```

## Execution Modes

### 1. Server Mode (Default)

Tools execute directly in the API server process. Best for trusted, lightweight operations.

**Characteristics:**
- ✅ Fastest execution (no overhead)
- ✅ Full access to server resources
- ⚠️ No isolation from other processes
- ⚠️ Errors can affect the server

**Use Cases:**
- Database queries
- API calls to trusted services
- File operations on server storage
- Cache operations

```python
from langchain_core.tools import tool
from structure.services.tools import server_tool


@tool("database_query")
@server_tool(timeout=60)
async def database_query(sql: str) -> dict:
    """Execute a database query."""
    # Runs directly in server process
    result = await db.execute(sql)
    return {"rows": result}
```

---

### 2. Sandbox Mode (Docker)

Tools execute in isolated Docker containers with configurable resource limits. Best for untrusted code execution.

**Characteristics:**
- ✅ Complete isolation from host system
- ✅ Configurable resource limits (CPU, memory, network)
- ✅ Timeout enforcement
- ⚠️ Higher latency (container startup)
- ⚠️ Requires Docker daemon

**Use Cases:**
- User-submitted code execution
- Running untrusted scripts
- Resource-intensive computations
- Multi-language code execution

```python
from langchain_core.tools import tool
from structure.services.tools import sandbox_tool


@tool("execute_python")
@sandbox_tool(
    image="python:3.12-slim",
    memory="256m",
    timeout=60,
    network=False,  # No network access
    cpu_percent=50,
)
async def execute_python(code: str) -> dict:
    """Execute Python code in isolated sandbox."""
    # Actual execution happens in Docker container
    pass
```

**Resource Limits:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `memory` | `"256m"` | Memory limit (e.g., "256m", "1g") |
| `cpu_percent` | `50` | CPU usage limit (% of one core) |
| `network` | `False` | Allow network access |
| `timeout` | `60` | Execution timeout in seconds |

**Sandbox Execution Flow:**

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│    Agent     │────▶│ SandboxExecutor │────▶│   Docker     │
│              │     │                 │     │  Container   │
└──────────────┘     └─────────────────┘     └──────────────┘
                              │                      │
                              │  1. Create container │
                              │  2. Start with limits│
                              │  3. Stream stdin     │
                              │  4. Wait for exit    │
                              │  5. Collect output   │
                              │  6. Cleanup          │
                              │◀─────────────────────│
                              │                      │
                              ▼
                     ┌─────────────────┐
                     │   ToolResult    │
                     │ (stdout/stderr) │
                     └─────────────────┘
```

---

### 3. Client Mode (Browser)

Tools execute in the user's browser via SSE events and HTTP callbacks. Best for accessing local user resources.

**Characteristics:**
- ✅ Access to user's local resources
- ✅ Native browser APIs (file picker, camera, geolocation)
- ✅ User controls permissions
- ⚠️ Requires active browser connection
- ⚠️ User interaction may be required

**Use Cases:**
- File selection from user's device
- Camera/microphone capture
- Clipboard access
- Geolocation
- Local storage access

```python
from langchain_core.tools import tool
from structure.services.tools import client_tool


@tool("select_file")
@client_tool(
    handler="filePicker",  # Frontend handler name
    timeout=120,
    config={"multiple": False},
)
async def select_file(
        allowed_extensions: list[str] = [".pdf", ".txt"],
) -> dict:
    """Open file picker in user's browser."""
    # Execution happens in browser
    pass
```

**Client Execution Flow:**

```
┌──────────┐    ┌───────────────┐    ┌─────────┐    ┌─────────┐
│  Agent   │───▶│ClientExecutor │───▶│  Redis  │───▶│   SSE   │
│          │    │               │    │ Stream  │    │ Client  │
└──────────┘    └───────────────┘    └─────────┘    └─────────┘
                       │                                  │
                       │  1. Store pending state          │
                       │  2. Publish TOOL_CLIENT_REQUEST  │
                       │                                  ▼
                       │                           ┌─────────────┐
                       │                           │   Browser   │
                       │                           │   Handler   │
                       │                           └─────────────┘
                       │                                  │
                       │  3. Wait for result              │
                       │◀─────────────────────────────────│
                       │     POST /tools/{id}/result      │
                       ▼
              ┌─────────────────┐
              │   ToolResult    │
              │ (from browser)  │
              └─────────────────┘
```

---

## Architecture Components

### ExecutionRouter

The central hub that routes tool execution to the appropriate executor.

```python
from structure.services.tools import ExecutionRouter, get_execution_router

# Get singleton router
router = get_execution_router()

# Or create custom instance
router = ExecutionRouter(
    sandbox_executor=my_sandbox_executor,
    client_executor=my_client_executor,
)

# Execute a tool
result = await router.execute(
    tool_name="execute_python",
    tool_id="unique-id-123",
    arguments={"code": "print('hello')"},
    context=execution_context,
)
```

### ToolMetadata

Defines how a tool should be executed.

```python
from structure.services.tools import ToolMetadata, ToolExecutionMode

metadata = ToolMetadata(
    execution_mode=ToolExecutionMode.SANDBOX,
    timeout_seconds=60,
    sandbox_image="python:3.12-slim",
    resource_limits=ResourceLimits(
        memory="512m",
        cpu_quota=100000,
        network_disabled=True,
    ),
)
```

### ExecutionContext

Provides context information for tool execution.

```python
from structure.schemas.tools.execution import ExecutionContext

context = ExecutionContext(
    run_id=uuid4(),
    workspace_id=uuid4(),
    user_id=uuid4(),
    conversation_id=uuid4(),
    correlation_id="trace-abc-123",
)
```

---

## Event Types

### TOOL_CLIENT_REQUEST

Sent to the browser when a client tool needs to be executed.

```json
{
  "event_type": "tool.client.request",
  "payload": {
    "tool_name": "select_file",
    "tool_id": "exec-uuid-123",
    "handler": "filePicker",
    "arguments": {
      "allowed_extensions": [".pdf", ".txt"],
      "multiple": false
    },
    "timeout_seconds": 120,
    "config": {}
  }
}
```

---

## API Endpoints

### Submit Tool Result

```http
POST /api/tools/{tool_id}/result
Content-Type: application/json

{
  "tool_id": "exec-uuid-123",
  "success": true,
  "result": {
    "name": "document.pdf",
    "size": 1024000,
    "type": "application/pdf",
    "content": "base64-encoded-content..."
  },
  "client_execution_time_ms": 1500
}
```

### Get Pending Tool Info

```http
GET /api/tools/{tool_id}/pending

Response:
{
  "tool_id": "exec-uuid-123",
  "tool_name": "select_file",
  "handler": "filePicker",
  "arguments": {"allowed_extensions": [".pdf"]},
  "timeout_seconds": 120,
  "expires_at": "2024-01-15T10:30:00Z"
}
```

---

## Frontend Integration

### Handling Client Tool Requests

```typescript
// TypeScript example for frontend

interface ToolClientRequest {
  tool_name: string;
  tool_id: string;
  handler: string;
  arguments: Record<string, any>;
  timeout_seconds: number;
  config: Record<string, any>;
}

// Listen for SSE events
const eventSource = new EventSource(`/api/runs/${runId}/events`);

eventSource.addEventListener('tool.client.request', async (event) => {
  const request: ToolClientRequest = JSON.parse(event.data);

  try {
    const result = await executeHandler(request);
    await submitResult(request.tool_id, { success: true, result });
  } catch (error) {
    await submitResult(request.tool_id, {
      success: false,
      error_message: error.message
    });
  }
});

// Handler registry
const handlers: Record<string, (req: ToolClientRequest) => Promise<any>> = {
  filePicker: async (req) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = req.arguments.allowed_extensions?.join(',');
    input.multiple = req.arguments.multiple ?? false;

    return new Promise((resolve) => {
      input.onchange = () => {
        const file = input.files?.[0];
        if (file) {
          const reader = new FileReader();
          reader.onload = () => resolve({
            name: file.name,
            size: file.size,
            type: file.type,
            content: reader.result,
          });
          reader.readAsDataURL(file);
        }
      };
      input.click();
    });
  },

  geolocation: async (req) => {
    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve({
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        }),
        (err) => reject(new Error(err.message)),
        { enableHighAccuracy: req.arguments.high_accuracy }
      );
    });
  },
};

async function executeHandler(request: ToolClientRequest) {
  const handler = handlers[request.handler];
  if (!handler) {
    throw new Error(`Unknown handler: ${request.handler}`);
  }
  return handler(request);
}

async function submitResult(toolId: string, result: any) {
  await fetch(`/api/tools/${toolId}/result`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tool_id: toolId, ...result }),
  });
}
```

---

## Security Considerations

### Sandbox Mode

| Risk | Mitigation |
|------|------------|
| Container escape | Use unprivileged containers, seccomp profiles |
| Resource exhaustion | Memory/CPU limits, PID limits |
| Network attacks | Disable network by default |
| Disk filling | Read-only root filesystem, tmpfs limits |
| Long-running processes | Strict timeouts with container kill |

### Client Mode

| Risk | Mitigation |
|------|------------|
| CSRF attacks | Verify tool_id matches active run |
| Data exfiltration | User controls all permissions |
| Timeout attacks | Server-side timeout enforcement |
| Replay attacks | Single-use tool_id, short TTL |

---

## Configuration

### Environment Variables

```bash
# Docker settings for sandbox
DOCKER_HOST=unix:///var/run/docker.sock

# Redis for client tool coordination
REDIS__HOST=localhost
REDIS__PORT=6379

# Default timeouts
TOOL_SANDBOX_TIMEOUT=60
TOOL_CLIENT_TIMEOUT=120
```

### Per-Tool Configuration

```python
# In your tool definition
@tool("my_sandbox_tool")
@sandbox_tool(
    image="custom-image:latest",
    memory="1g",
    timeout=300,
    network=True,  # Allow network for this specific tool
)
async def my_sandbox_tool(...):
    pass
```

---

## Monitoring & Debugging

### Logging

All executors log execution details:

```python
import logging
logging.getLogger("structure.services.agent.tools").setLevel(logging.DEBUG)
```

### Metrics

Key metrics to monitor:

- `tool_execution_duration_ms` - Execution time by mode
- `tool_execution_success_rate` - Success/failure ratio
- `sandbox_container_count` - Active sandbox containers
- `client_tool_timeout_rate` - Client response timeouts

### Debugging Client Tools

1. Check SSE connection is active
2. Verify `TOOL_CLIENT_REQUEST` event is received
3. Check browser console for handler errors
4. Verify result POST reaches the server
5. Check Redis for pending execution state

---

## Example: Complete Tool Definition

```python
"""
Complete example showing all three execution modes.
"""

from langchain_core.tools import tool
from structure.services.tools import (
    sandbox_tool,
    client_tool,
    server_tool,
)


# Server tool - fast, trusted operations
@tool("search_database")
@server_tool(timeout=30)
async def search_database(query: str, limit: int = 10) -> dict:
    """Search the database for matching records."""
    from structure.extensions.database import get_session

    async with get_session("structure") as db:
        result = await db.execute(
            "SELECT * FROM records WHERE content LIKE :q LIMIT :l",
            {"q": f"%{query}%", "l": limit}
        )
        return {"records": result.fetchall()}


# Sandbox tool - isolated code execution
@tool("run_analysis")
@sandbox_tool(
    image="python:3.12-slim",
    memory="512m",
    timeout=120,
    network=False,
)
async def run_analysis(code: str, data: dict) -> dict:
    """Run custom analysis code on provided data."""
    # Code executes in isolated container
    # Input: code + data as JSON
    # Output: analysis results
    pass


# Client tool - user interaction required
@tool("upload_document")
@client_tool(handler="documentUpload", timeout=180)
async def upload_document(
        accepted_types: list[str] = ["application/pdf", "text/plain"],
        max_size_mb: int = 10,
) -> dict:
    """Allow user to upload a document from their device."""
    # Browser handles file selection
    # Returns file metadata and content
    pass
```

---

## Migration Guide

### From Direct Tool Execution

Before:
```python
@tool("execute_code")
async def execute_code(code: str) -> dict:
    exec(code)  # Dangerous!
    return {"status": "ok"}
```

After:
```python
@tool("execute_code")
@sandbox_tool(image="python:3.12-slim", memory="256m")
async def execute_code(code: str) -> dict:
    """Now executes safely in isolated container."""
    pass
```

### Adding Client Tools

1. Define tool with `@client_tool` decorator
2. Implement frontend handler
3. Register handler in frontend handler registry
4. Test SSE event flow

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Sandbox timeout | Increase timeout or optimize code |
| Docker not available | Install Docker, check daemon running |
| Client tool not responding | Check SSE connection, browser console |
| Redis connection error | Verify Redis config, check connectivity |
| Permission denied (sandbox) | Check Docker socket permissions |

---

## Related Documentation

- [Agent System Architecture](./agent-architecture.md)
- [Event System](./event-system.md)
- [API Reference](./api-reference.md)
