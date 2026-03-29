# Server Tools Configuration Guide

Complete guide for configuring server-side tools in agents.

## Overview

Server tools are built-in tools that execute directly in the API server process. They provide fast, trusted operations for:
- **Context & Knowledge**: Search contexts, query databases, retrieve conversation history
- **File Operations**: List files, read content, search documents
- **Utilities**: Get time, workspace info, run history, cache operations

## Available Tool Groups

### 1. CONTEXT_TOOLS

Tools for querying context and knowledge:

| Tool | Description | Use Case |
|------|-------------|----------|
| `search_context` | Regex search in context content | Find specific patterns in conversation history |
| `get_run_memory` | Retrieve conversation history | Access previous messages for context |
| `query_structured_data` | Execute SQL queries | Query database tables |

**Example Use Cases:**
- "Search for all error messages in our conversation"
- "Show me the last 10 messages"
- "Query user data from the database"

### 2. FILE_TOOLS

Tools for file system operations:

| Tool | Description | Use Case |
|------|-------------|----------|
| `list_workspace_files` | List files in workspace | Browse workspace documents |
| `read_file_content` | Read file content | Access document content |
| `search_files` | Search files by name/content | Find specific documents |

**Example Use Cases:**
- "List all PDF files in my workspace"
- "Read the content of report.txt"
- "Search for files containing 'budget'"

### 3. UTILITY_TOOLS

General utility tools:

| Tool | Description | Use Case |
|------|-------------|----------|
| `get_current_time` | Get server time | Show current time in any timezone |
| `get_workspace_info` | Get workspace details | Display workspace metadata |
| `get_run_history` | Get recent runs | Show execution history |
| `cache_get` | Get cached value | Retrieve from Redis |
| `cache_set` | Set cached value | Store in Redis |

**Example Use Cases:**
- "What time is it in Tokyo?"
- "Show me workspace information"
- "Get the last 5 runs"
- "Cache this value for later"

## Configuration Methods

### Method 1: Boolean (Simple)

Enable or disable all server tools:

```python
# Disable all (default)
config = {
    "server_tools": False  # or omit the key
}

# Enable all
config = {
    "server_tools": True
}
```

### Method 2: List (Selective Groups)

Enable specific tool groups:

```python
# Enable only utility tools
config = {
    "server_tools": ["utility"]
}

# Enable context and file tools
config = {
    "server_tools": ["context", "file"]
}

# Enable all groups explicitly
config = {
    "server_tools": ["context", "file", "utility"]
}

# Shorthand for all
config = {
    "server_tools": ["all"]
}
```

### Method 3: Dict (Fine-Grained Control)

Enable/disable individual groups:

```python
# Enable only context and utility
config = {
    "server_tools": {
        "context": True,
        "file": False,
        "utility": True
    }
}

# Enable only file tools
config = {
    "server_tools": {
        "context": False,
        "file": True,
        "utility": False
    }
}
```

## Complete Configuration Example

```python
from structure.plugins.executors.default import (
    DefaultAgentTemplate,
)

# Full configuration with all options
config = {
    # LLM settings
    "model_provider": "tongyi",
    "model_name": "qwen-plus",
    "max_history_messages": 20,

    # Browser tools (for web scraping, screenshots, etc.)
    "enable_browser_tools": True,

    # Server tools (built-in server-side operations)
    "server_tools": {
        "context": True,  # Search contexts, query DB, get history
        "file": True,  # File operations
        "utility": True,  # Time, workspace info, cache
    },

    # Tools requiring approval
    "approval_tools": ["query_structured_data"],
}

agent = DefaultAgentTemplate(config=config)
```

## Usage Patterns

### Pattern 1: Minimal Agent (No Tools)

For simple chat without any special capabilities:

```python
config = {
    "model_provider": "tongyi",
    "model_name": "qwen-plus",
    "enable_browser_tools": False,
    "server_tools": False,  # Default
}
```

### Pattern 2: Utility-Only Agent

For agents that need time/workspace info but nothing else:

```python
config = {
    "enable_browser_tools": False,
    "server_tools": ["utility"],
}
```

**Good for:**
- Showing current time in responses
- Displaying workspace information
- Simple cache operations

### Pattern 3: Knowledge Agent

For agents that need to query context and knowledge:

```python
config = {
    "enable_browser_tools": False,
    "server_tools": ["context"],
}
```

**Good for:**
- Searching through conversation history
- Querying structured data
- Finding patterns in context

### Pattern 4: Document Agent

For agents working with files:

```python
config = {
    "enable_browser_tools": False,
    "server_tools": ["file", "utility"],
}
```

**Good for:**
- Document browsing and reading
- File search
- Content analysis

### Pattern 5: Full-Featured Agent

For agents with all capabilities:

```python
config = {
    "enable_browser_tools": True,
    "server_tools": True,  # All groups
}
```

**Good for:**
- Production assistants
- Multi-purpose agents
- Power users

### Pattern 6: Custom Combination

Mix browser, server, and user tools:

```python
# Initialize with specific server tools
config = {
    "enable_browser_tools": True,
    "server_tools": ["context", "utility"],
}

# User tools are loaded separately via ToolRegistry
from structure.services.tools.dynamic_tool_loader import DynamicToolLoader

loader = DynamicToolLoader(db)
await loader.load_user_tools(user_id)

# Agent will have: browser + context + utility + user tools
agent = DefaultAgentTemplate(config=config)
```

## Security Considerations

### SQL Query Tool

The `query_structured_data` tool has built-in security:
- Only `SELECT` queries allowed
- Blocks dangerous keywords: `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`, `EXEC`
- Automatic `LIMIT` clause if not present
- Parameter binding support

**Example:**
```python
# Safe query
result = await query_structured_data(
    sql="SELECT * FROM users WHERE id = :user_id",
    params={"user_id": "123"}
)

# Blocked queries
await query_structured_data("DROP TABLE users")  # ERROR
await query_structured_data("DELETE FROM users")  # ERROR
```

### File Access

File tools only access files within workspace boundaries:
- Scoped by `workspace_id`
- Cannot access arbitrary file system paths
- Limited to database-registered documents

### Cache Operations

Cache tools use namespaced keys:
- Keys are prefixed with namespace
- Configurable TTL
- Isolated per user/workspace

## Performance Considerations

### Tool Execution Times

| Tool Group | Avg. Execution Time | Notes |
|------------|---------------------|-------|
| CONTEXT_TOOLS | 50-500ms | Database queries |
| FILE_TOOLS | 100-1000ms | Storage I/O |
| UTILITY_TOOLS | 5-50ms | Very fast |

### Recommendations

1. **Enable only needed tools**: Reduces LLM confusion and improves response time
2. **Use caching**: Cache frequently accessed data with `cache_set/get`
3. **Limit query results**: Use `max_results`, `limit` parameters
4. **Monitor timeouts**: Each tool has configurable timeout (5-60s)

## Troubleshooting

### Tool Not Found

**Problem:** Agent says "Tool not found" or doesn't call the tool

**Solutions:**
1. Verify tool group is enabled in config
2. Check tool name matches exactly
3. Ensure agent reinitialized after config change

```python
# Wrong: typo in group name
config = {"server_tools": ["untility"]}  # Should be "utility"

# Correct
config = {"server_tools": ["utility"]}
```

### Permission Errors

**Problem:** "Query contains forbidden keyword" or "Access denied"

**Solutions:**
1. Check SQL query doesn't use forbidden keywords
2. Verify file is within workspace
3. Ensure proper workspace_id/user_id

### Timeout Errors

**Problem:** Tool execution times out

**Solutions:**
1. Increase timeout in tool metadata
2. Reduce query scope (fewer results, smaller files)
3. Use pagination for large datasets

```python
# Limit results to avoid timeout
result = await search_context(
    pattern="error",
    max_results=10  # Instead of default 100
)
```

## Testing Tools

### Direct Tool Testing

Test tools before using in agent:

```python
from structure.services.tools.inner_tool.server_tools import get_current_time

# Test directly
result = await get_current_time(
    timezone="Asia/Shanghai",
    format="%Y-%m-%d %H:%M:%S"
)
print(result)  # {'time': '2026-02-08 14:30:00', 'timezone': 'Asia/Shanghai', ...}
```

### Agent Testing

Test tools through agent:

```python
from structure.schemas.events.event_payloads import UserMessage

message = UserMessage(message="What time is it in Tokyo?")
result = await agent.run(message)
print(result['answer'])
```

## API Integration

### Via Configuration Endpoint

If your system supports dynamic agent configuration:

```bash
# Create agent with server tools
POST /api/agents
{
  "name": "My Assistant",
  "model": "qwen-plus",
  "config": {
    "server_tools": ["utility", "context"]
  }
}
```

### Via Agent Template

Define in template configuration:

```python
TEMPLATE = {
    "template_code": "CustomAgent",
    "template_name": "Custom Agent with Tools",
    "config": {
        "model": {"provider": "tongyi", "name": "qwen-plus"},
        "server_tools": {
            "context": True,
            "file": False,
            "utility": True
        }
    }
}
```

## Best Practices

1. **Start minimal**: Enable only tools you need
2. **Test incrementally**: Add tool groups one at a time
3. **Document usage**: Clearly document which tools are available to users
4. **Monitor usage**: Track which tools are actually being used
5. **Set approval requirements**: Use `approval_tools` for sensitive operations
6. **Provide clear descriptions**: Update tool descriptions for your domain
7. **Version control**: Track configuration changes in version control

## Migration Guide

### From Old Configuration

If you're migrating from an older version:

```python
# Old way (only browser tools)
config = {
    "enable_browser_tools": True
}

# New way (browser + server tools)
config = {
    "enable_browser_tools": True,
    "server_tools": ["utility"]  # Add server tools
}
```

### Adding Server Tools to Existing Agent

```python
# Step 1: Update configuration
agent_config["server_tools"] = ["utility", "context"]

# Step 2: Reinitialize agent
agent = DefaultAgentTemplate(config=agent_config)

# Step 3: Test
message = UserMessage(message="What time is it?")
result = await agent.run(message)
```

## Examples

See complete working examples in:
- `examples/agent_with_server_tools.py` - Comprehensive examples
- `examples/agent_with_user_tools.py` - Combining with user tools
- `examples/user_tools_demo.py` - User tool creation

## Reference

### Configuration Schema

```python
{
    "server_tools": (
        False |  # Disable all
        True |   # Enable all
        ["context", "file", "utility"] |  # Enable specific groups
        {  # Fine-grained control
            "context": bool,
            "file": bool,
            "utility": bool
        }
    )
}
```

### Available Tool Names

**Context Tools:**
- `search_context`
- `get_run_memory`
- `query_structured_data`

**File Tools:**
- `list_workspace_files`
- `read_file_content`
- `search_files`

**Utility Tools:**
- `get_current_time`
- `get_workspace_info`
- `get_run_history`
- `cache_get`
- `cache_set`

## Support

For questions or issues:
1. Check this documentation
2. Review examples in `examples/`
3. Check tool source code in `src/structure/services/executor/tools/server_tools.py`
4. Report issues at project repository
