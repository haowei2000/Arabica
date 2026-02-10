# User Tools Guide

Complete guide on creating and using custom tools in agents.

## Overview

The user tools system allows users to:
1. **Create custom tools** via API with their own logic
2. **Store tools** in the database with versioning
3. **Dynamically load** tools at runtime
4. **Use tools in agents** automatically through function calling

## Architecture

```
User → API → Database → Dynamic Loader → Tool Registry → Agent
```

## Step-by-Step Guide

### Step 1: Create a Custom Tool

**API Endpoint:** `POST /tools/`

**Example: Calculator Tool**

```json
{
  "name": "calculator",
  "display_name": "Calculator",
  "description": "Performs basic arithmetic operations (add, subtract, multiply, divide)",
  "execution_mode": "server_run",
  "category": "math",
  "tags": ["math", "calculation"],
  "timeout": 10,
  "input_schema": {
    "type": "object",
    "properties": {
      "operation": {
        "type": "string",
        "description": "Operation: add, subtract, multiply, divide",
        "enum": ["add", "subtract", "multiply", "divide"]
      },
      "a": {
        "type": "number",
        "description": "First number"
      },
      "b": {
        "type": "number",
        "description": "Second number"
      }
    },
    "required": ["operation", "a", "b"]
  },
  "code": "operations = {'add': lambda a,b: a+b, 'subtract': lambda a,b: a-b, 'multiply': lambda a,b: a*b, 'divide': lambda a,b: a/b if b!=0 else 'Division by zero'}\\nresult = {'value': operations[input_data['operation']](input_data['a'], input_data['b']), 'operation': input_data['operation']}"
}
```

**Response:**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "name": "calculator",
  "display_name": "Calculator",
  "description": "Performs basic arithmetic operations",
  "enabled": true,
  "created_at": "2026-02-08T10:00:00Z"
}
```

### Step 2: Load Tools into Agent

**API Endpoint:** `POST /tools/load`

```bash
curl -X POST "http://localhost:8000/tools/load"
```

**Response:**
```json
{
  "success": true,
  "message": "Loaded 1 tools",
  "tools": ["calculator"]
}
```

### Step 3: Verify Tool Registration

**API Endpoint:** `GET /tools/registry/schemas`

```bash
curl "http://localhost:8000/tools/registry/schemas?format=openai"
```

**Response:**
```json
{
  "schemas": [
    {
      "type": "function",
      "function": {
        "name": "calculator",
        "description": "Performs basic arithmetic operations (add, subtract, multiply, divide)",
        "parameters": {
          "type": "object",
          "properties": {
            "operation": {
              "type": "string",
              "description": "Operation: add, subtract, multiply, divide"
            },
            "a": {
              "type": "number",
              "description": "First number"
            },
            "b": {
              "type": "number",
              "description": "Second number"
            }
          },
          "required": ["operation", "a", "b"]
        }
      }
    }
  ]
}
```

### Step 4: Use in Agent

**Python Code:**

```python
from aiwen.services.tools.tool_registry import ToolRegistry
from aiwen.services.tools.dynamic_tool_loader import DynamicToolLoader

# In your agent initialization
async def initialize_agent(user_id: UUID, db: AsyncSession):
    # Load user tools
    loader = DynamicToolLoader(db)
    await loader.load_user_tools(user_id)

    # Get all tool schemas for LLM
    tool_schemas = ToolRegistry.get_all_schemas(format="openai")

    # Pass to your LLM (example with OpenAI)
    from openai import AsyncOpenAI

    client = AsyncOpenAI()

    response = await client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant with access to tools."},
            {"role": "user", "content": "What is 15 + 27?"}
        ],
        tools=tool_schemas,  # Pass tool schemas
        tool_choice="auto"
    )

    # Handle tool calls
    if response.choices[0].message.tool_calls:
        for tool_call in response.choices[0].message.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)

            # Execute tool
            tool_instance = ToolRegistry.get_tool_instance(tool_name)
            result = await tool_instance(**tool_args)

            print(f"Tool result: {result}")
```

## More Examples

### Example 1: Weather Tool (HTTP Mode)

```json
{
  "name": "weather_check",
  "display_name": "Weather Checker",
  "description": "Get current weather for a city",
  "execution_mode": "http",
  "input_schema": {
    "type": "object",
    "properties": {
      "city": {"type": "string", "description": "City name"}
    },
    "required": ["city"]
  },
  "http_config": {
    "method": "GET",
    "url": "https://api.openweathermap.org/data/2.5/weather",
    "headers": {"Accept": "application/json"},
    "timeout": 10
  }
}
```

### Example 2: Text Analyzer Tool

```json
{
  "name": "text_analyzer",
  "display_name": "Text Analyzer",
  "description": "Analyzes text and returns word count, character count, etc.",
  "execution_mode": "server_run",
  "input_schema": {
    "type": "object",
    "properties": {
      "text": {"type": "string", "description": "Text to analyze"}
    },
    "required": ["text"]
  },
  "code": "text = input_data['text']\\nresult = {'word_count': len(text.split()), 'char_count': len(text), 'line_count': len(text.splitlines()), 'words': text.split()}"
}
```

### Example 3: Database Query Tool

```json
{
  "name": "db_query_users",
  "display_name": "Query Users",
  "description": "Get list of users from database",
  "execution_mode": "server_run",
  "input_schema": {
    "type": "object",
    "properties": {
      "limit": {"type": "integer", "description": "Max results", "default": 10}
    }
  },
  "code": "from sqlalchemy import select, text\\nfrom aiwen.extensions.database import get_session\\nimport asyncio\\n\\nasync def query():\\n    async with get_session('aiwen') as db:\\n        result = await db.execute(text(f\\\"SELECT id, name FROM users LIMIT {input_data.get('limit', 10)}\\\"))\\n        return [{'id': str(row[0]), 'name': row[1]} for row in result.fetchall()]\\n\\nresult = {'users': asyncio.run(query())}"
}
```

## Testing Tools

### Direct Execution

**API Endpoint:** `POST /tools/execute`

```json
{
  "tool_id": "123e4567-e89b-12d3-a456-426614174000",
  "parameters": {
    "operation": "add",
    "a": 15,
    "b": 27
  }
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "value": 42,
    "operation": "add"
  },
  "execution_time": 0.002
}
```

## Tool Management

### List Tools

```bash
GET /tools/?enabled_only=true&include_public=true
```

### Update Tool

```bash
PATCH /tools/{tool_id}
Content-Type: application/json

{
  "description": "Updated description",
  "enabled": false
}
```

### Delete Tool

```bash
DELETE /tools/{tool_id}
```

### Reload Tool (after update)

```bash
POST /tools/{tool_id}/reload
```

## Security Considerations

1. **Code Execution Safety**
   - User code runs in the same process (server_run mode)
   - Consider using container_run mode for untrusted code
   - Implement code review/verification system

2. **Input Validation**
   - All inputs are validated against the JSON Schema
   - Pydantic ensures type safety

3. **Rate Limiting**
   - Implement rate limits on tool creation
   - Limit tool execution frequency

4. **Permissions**
   - Users can only modify their own tools
   - Public tools are read-only for non-owners

## Best Practices

1. **Clear Descriptions**: Write clear tool descriptions for LLM to understand
2. **Specific Input Schemas**: Define precise input parameters
3. **Error Handling**: Handle errors gracefully in tool code
4. **Testing**: Test tools thoroughly before using in production
5. **Versioning**: Use version field to track tool changes
6. **Categories**: Organize tools with categories and tags

## Advanced: LangChain Integration

```python
from langchain.agents import create_openai_tools_agent
from langchain.tools import StructuredTool

# Convert registered tools to LangChain tools
def create_langchain_tools():
    tools = []

    for tool_name in ToolRegistry.list_tools():
        tool_instance = ToolRegistry.get_tool_instance(tool_name)
        tool_class = ToolRegistry.get_tool_class(tool_name)

        # Create LangChain StructuredTool
        lc_tool = StructuredTool(
            name=tool_class.METADATA.name,
            description=tool_class.METADATA.description,
            func=tool_instance.__call__,
            args_schema=tool_class.InputSchema
        )
        tools.append(lc_tool)

    return tools

# Use in agent
tools = create_langchain_tools()
agent = create_openai_tools_agent(llm, tools, prompt)
```

## Troubleshooting

### Tool Not Found
- Ensure tool is loaded: `POST /tools/load`
- Check if tool is enabled
- Verify user permissions

### Execution Errors
- Check tool code syntax
- Review input parameters
- Check execution logs

### Performance Issues
- Set appropriate timeouts
- Consider using async operations
- Use container_run for heavy workloads

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/tools/` | POST | Create tool |
| `/tools/` | GET | List tools |
| `/tools/{id}` | GET | Get tool |
| `/tools/{id}` | PATCH | Update tool |
| `/tools/{id}` | DELETE | Delete tool |
| `/tools/load` | POST | Load tools into agent |
| `/tools/{id}/reload` | POST | Reload specific tool |
| `/tools/execute` | POST | Execute tool directly |
| `/tools/registry/all` | GET | Get registered tools |
| `/tools/registry/schemas` | GET | Get tool schemas |
