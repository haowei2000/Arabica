# User Tools Architecture

Complete architecture documentation for the user-defined tools system.

## Overview

The user tools system enables users to create, manage, and execute custom tools within agents through a five-layer architecture:

```
┌─────────────────────────────────────────────────────────────────┐
│                        API Layer                                 │
│  (FastAPI endpoints for tool CRUD)                              │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────────┐
│                     Storage Layer                                │
│  (PostgreSQL + SQLAlchemy models)                               │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────────┐
│                    Loading Layer                                 │
│  (DynamicToolLoader - runtime class creation)                   │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────────┐
│                    Registry Layer                                │
│  (ToolRegistry - centralized tool management)                   │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────────┐
│                   Execution Layer                                │
│  (Agent + LangChain + ExecutionRouter)                          │
└─────────────────────────────────────────────────────────────────┘
```

## Component Details

### 1. API Layer

**File:** `src/structure/routers/tools/user_tools.py`

**Responsibilities:**
- Expose REST API for tool CRUD operations
- Handle authentication and authorization
- Validate input schemas
- Trigger tool loading/reloading

**Key Endpoints:**
```python
POST   /tools/              # Create tool
GET    /tools/              # List tools
GET    /tools/{id}          # Get tool
PATCH  /tools/{id}          # Update tool
DELETE /tools/{id}          # Delete tool
POST   /tools/load          # Load all user tools
POST   /tools/{id}/reload   # Reload specific tool
POST   /tools/execute       # Execute tool (testing)
GET    /tools/registry/schemas  # Get LLM schemas
```

### 2. Storage Layer

**Files:**
- `src/structure/models/tools/user_tool.py` - SQLAlchemy model
- `src/structure/schemas/tools/user_tool.py` - Pydantic schemas
- `src/structure/services/tools/user_tool_crud.py` - CRUD operations

**Database Schema:**
```sql
CREATE TABLE user_tools (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    workspace_id UUID,
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(200),
    description TEXT,
    execution_mode VARCHAR(50),  -- server_run, http, client_run, etc.
    input_schema JSONB,
    output_schema JSONB,
    code TEXT,                   -- For server_run mode
    http_config JSONB,           -- For http mode
    container_config JSONB,      -- For container_run mode
    client_config JSONB,         -- For client_run mode
    celery_config JSONB,         -- For celery_run mode
    category VARCHAR(100),
    tags TEXT[],
    version VARCHAR(20),
    timeout INTEGER,
    enabled BOOLEAN DEFAULT true,
    is_public BOOLEAN DEFAULT false,
    verified BOOLEAN DEFAULT false,
    usage_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE UNIQUE INDEX idx_user_tools_user_name ON user_tools(user_id, name);
CREATE INDEX idx_user_tools_workspace ON user_tools(workspace_id);
CREATE INDEX idx_user_tools_enabled ON user_tools(enabled);
```

**CRUD Operations:**
- `create_tool()` - Create and validate tool
- `get_tool_by_id()` - Retrieve tool by ID
- `list_user_tools()` - List tools with filters
- `update_tool()` - Update tool (owner only)
- `delete_tool()` - Delete tool (owner only)
- `toggle_enabled()` - Enable/disable tool
- `increment_usage()` - Track usage statistics

### 3. Loading Layer

**File:** `src/structure/services/tools/dynamic_tool_loader.py`

**Responsibilities:**
- Read tool definitions from database
- Dynamically create Python classes at runtime
- Convert JSON Schema to Pydantic models
- Register tools with ToolRegistry

**Key Methods:**

```python
class DynamicToolLoader:
    def _create_input_schema(
        self, tool_name: str, schema_dict: dict
    ) -> type[ToolInputSchema]:
        """Convert JSON Schema to Pydantic model using create_model()"""
        # Maps JSON Schema types to Python types
        # Creates dynamic Pydantic model

    def _create_tool_class(
        self, user_tool: UserTool
    ) -> type[BaseTool]:
        """Create complete tool class using type()"""
        # Creates InputSchema
        # Parses execution mode configs
        # Defines execute method
        # Returns dynamically created class

    async def load_user_tools(
        self, user_id: UUID, workspace_id: UUID | None = None
    ) -> list[str]:
        """Load all enabled tools for user and register them"""
```

**Dynamic Class Creation:**

```python
# Input schema creation
InputSchema = create_model(
    f"{tool_name}InputSchema",
    __base__=ToolInputSchema,
    **field_definitions  # From JSON Schema
)

# Tool class creation
ToolClass = type(
    f"UserTool_{name}_{id}",
    (BaseTool,),
    {
        "METADATA": metadata,
        "InputSchema": InputSchema,
        "OutputSchema": ToolOutputSchema,
        "execute": execute_method,
    }
)
```

### 4. Registry Layer

**File:** `src/structure/services/executor/tools/tool_registry.py`

**Responsibilities:**
- Centralized registry for all tools (built-in + user-defined)
- Singleton pattern for tool instances
- Schema generation for LLMs
- Tool querying by mode/category/tags

**Key Methods:**

```python
class ToolRegistry:
    @classmethod
    def register(cls, tool_class: type[BaseTool]):
        """Register a tool class"""

    @classmethod
    def unregister(cls, tool_name: str):
        """Remove tool from registry"""

    @classmethod
    def get_tool_instance(cls, tool_name: str) -> BaseTool | None:
        """Get singleton instance"""

    @classmethod
    def get_all_schemas(cls, format: str = "openai") -> list[dict]:
        """Get schemas for LLM function calling"""
        # Supports: openai, langchain, anthropic

    @classmethod
    def list_tools(
        cls,
        execution_mode: ToolExecutionMode | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Query registered tools"""
```

### 5. Execution Layer

**File:** `src/structure/services/executor/executor_template/default/concrete.py`

**Responsibilities:**
- Initialize agent with all tools (built-in + user)
- Convert tools to LangChain format
- Handle tool execution through ExecutionRouter
- Stream events to frontend

**Integration Points:**

```python
class DefaultAgentTemplate(Executor):
    def __init__(self, config: dict):
        # 1. Collect tools
        tools = []
        if self.enable_browser_tools:
            tools.extend(BROWSER_TOOLS)

        # 2. Load user tools from registry
        user_tools = self._load_registry_tools()
        tools.extend(user_tools)

        # 3. Create agent with all tools
        self.agent = create_agent(
            model=self.llm,
            tools=tools,
            system_prompt=system_prompt
        )

        # 4. Register with execution router
        self._execution_router = ExecutionRouter()
        self._execution_router.register_tools_from_list(tools)

    def _load_registry_tools(self) -> list:
        """Convert ToolRegistry tools to LangChain format"""
        from langchain_core.tools import StructuredTool

        langchain_tools = []
        for tool_name in ToolRegistry.list_tools():
            tool_class = ToolRegistry.get_tool_class(tool_name)
            tool_instance = ToolRegistry.get_tool_instance(tool_name)

            lc_tool = StructuredTool(
                name=tool_class.METADATA.name,
                description=tool_class.METADATA.description,
                coroutine=tool_instance.__call__,
                args_schema=tool_class.InputSchema,
            )
            langchain_tools.append(lc_tool)

        return langchain_tools
```

## Execution Modes

The system supports five execution modes:

### 1. SERVER_RUN (In-Process Execution)

**Use Case:** Fast, simple Python code execution
**Security:** Low - runs in same process
**Example:** Calculator, text processing

```python
# Tool code executed with exec()
context = {
    "input_data": input_data.model_dump(),
    "__builtins__": __builtins__,
}
exec(tool_code, context)
result = context["result"]
```

### 2. HTTP (External API Call)

**Use Case:** Third-party APIs, web services
**Security:** High - isolated external service
**Example:** Weather API, translation service

```python
http_config = {
    "method": "GET",
    "url": "https://api.example.com/endpoint",
    "headers": {"Authorization": "Bearer token"},
    "timeout": 10
}
```

### 3. CLIENT_RUN (Client-Side Execution)

**Use Case:** Browser automation, file access
**Security:** Medium - runs on client machine
**Example:** Screenshot, file upload

```python
# Server sends request to client
yield TOOL_CLIENT_REQUEST
# Wait for client response
raise WaitingForTool(...)
```

### 4. CONTAINER_RUN (Sandboxed Container)

**Use Case:** Untrusted code, heavy workloads
**Security:** High - isolated container
**Example:** Code execution, data processing

```python
container_config = {
    "image": "python:3.12",
    "command": ["python", "script.py"],
    "memory_limit": "512m",
    "timeout": 60
}
```

### 5. CELERY_RUN (Async Task Queue)

**Use Case:** Long-running tasks, scheduled jobs
**Security:** Medium - separate worker process
**Example:** Video processing, batch operations

```python
celery_config = {
    "queue": "default",
    "priority": 5,
    "retry": True,
    "max_retries": 3
}
```

## Data Flow

### Tool Creation Flow

```
User → API → Validation → Database → Response
  1. POST /tools/ with tool definition
  2. Validate input schema (Pydantic)
  3. Check name uniqueness
  4. Store in database
  5. Return tool ID
```

### Tool Loading Flow

```
Request → Database → DynamicLoader → ToolRegistry → Agent
  1. POST /tools/load
  2. Fetch enabled tools from DB
  3. Create tool classes dynamically
  4. Register in ToolRegistry
  5. Agent picks up tools on next init
```

### Tool Execution Flow

```
User Message → LLM → Tool Call → Execution → Result → LLM → Response
  1. User sends message
  2. LLM decides to call tool (function calling)
  3. Agent extracts tool_name + arguments
  4. Get tool instance from ToolRegistry
  5. Route execution based on mode:
     - SERVER_RUN: Execute code directly
     - HTTP: Send HTTP request
     - CLIENT: Request from client
     - CONTAINER: Launch container
     - CELERY: Queue task
  6. Return result to LLM
  7. LLM generates final response
```

## Security Considerations

### 1. Code Execution (SERVER_RUN)

**Risks:**
- Arbitrary code execution in server process
- Access to server resources
- Potential for infinite loops

**Mitigations:**
- Timeout limits (max 3600 seconds)
- User isolation (tools belong to user)
- Consider using CONTAINER_RUN for untrusted code
- Implement code review system
- Rate limiting on tool creation

### 2. Input Validation

**Protections:**
- JSON Schema validation on input
- Pydantic type checking
- SQL injection prevention (SQLAlchemy)
- XSS prevention (no HTML rendering)

### 3. Access Control

**Permissions:**
- Users can only modify own tools
- Public tools are read-only
- Workspace isolation
- Tool execution requires authentication

### 4. Resource Limits

**Constraints:**
- Timeout per tool execution
- Rate limiting on API endpoints
- Database connection pooling
- Memory limits for containers

## Performance Optimization

### 1. Tool Loading

- **Lazy Loading**: Tools loaded only when needed
- **Caching**: ToolRegistry caches instances
- **Hot Reload**: Update tools without restart

### 2. Execution

- **Async Operations**: All I/O is async
- **Connection Pooling**: Database connections reused
- **Parallel Execution**: Independent tools run concurrently

### 3. Database

- **Indexes**: On user_id, workspace_id, enabled
- **Prepared Statements**: Query optimization
- **Bulk Operations**: Batch tool loading

## Testing

### Unit Tests

```python
# Test tool CRUD
async def test_create_tool():
    tool = await crud.create_tool(user_id, tool_data)
    assert tool.name == "calculator"

# Test dynamic loading
async def test_load_tool():
    loader = DynamicToolLoader(db)
    tools = await loader.load_user_tools(user_id)
    assert "calculator" in tools

# Test execution
async def test_execute_tool():
    instance = ToolRegistry.get_tool_instance("calculator")
    result = await instance(operation="add", a=5, b=3)
    assert result["data"]["value"] == 8
```

### Integration Tests

```python
# Test end-to-end flow
async def test_agent_with_user_tools():
    # Create tool
    tool = await crud.create_tool(user_id, tool_data)

    # Load into agent
    await loader.load_user_tools(user_id)
    agent = DefaultAgentTemplate(config)

    # Execute
    result = await agent.run(user_message)
    assert "42" in result["answer"]
```

## Monitoring

### Metrics to Track

1. **Usage Statistics**
   - Tool execution count
   - Success/failure rate
   - Average execution time

2. **Performance**
   - Tool loading time
   - Database query latency
   - Agent response time

3. **Errors**
   - Tool execution failures
   - Validation errors
   - Timeout occurrences

### Logging

```python
logger.info(f"Tool executed: {tool_name}")
logger.error(f"Tool execution failed: {error}", exc_info=True)
```

## Future Enhancements

1. **Tool Marketplace**
   - Public tool sharing
   - Tool ratings and reviews
   - Tool templates

2. **Advanced Features**
   - Tool versioning and rollback
   - A/B testing for tools
   - Tool composition (chaining)
   - Tool analytics dashboard

3. **Security Enhancements**
   - Code scanning for vulnerabilities
   - Sandboxed execution for all modes
   - Tool permission system
   - Audit logging

4. **Developer Experience**
   - Visual tool builder UI
   - Tool testing framework
   - Debug mode for tools
   - Tool documentation generator

## Troubleshooting

### Common Issues

**Problem:** Tool not found after creation
**Solution:** Call `POST /tools/load` to load tools into registry

**Problem:** Tool execution timeout
**Solution:** Increase timeout in tool config or optimize code

**Problem:** Import error in SERVER_RUN code
**Solution:** Ensure required packages are installed on server

**Problem:** Tool not appearing in agent
**Solution:** Check tool is enabled and agent is reinitialized

### Debug Tips

1. Check tool registration: `GET /tools/registry/all`
2. Verify tool schema: `GET /tools/registry/schemas`
3. Test tool directly: `POST /tools/execute`
4. Check logs for errors
5. Verify database records

## References

- **API Documentation**: `docs/user_tools_guide.md`
- **Example Code**: `examples/user_tools_demo.py`
- **Integration Example**: `examples/agent_with_user_tools.py`
- **Base Tool Class**: `src/structure/services/executor/tools/base_tool.py`
- **Tool Registry**: `src/structure/services/executor/tools/tool_registry.py`
