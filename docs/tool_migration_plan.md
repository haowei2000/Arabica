# Tool System Migration Plan

## Current State

### Tools Using LangChain @tool Decorator

| File | Tools Count | Status |
|------|-------------|--------|
| `server_tools.py` | 11 tools | ⏳ To migrate |
| `browser_tools.py` | 14 tools | ⏳ To migrate |
| `example_tools.py` | 10 tools | ⏳ To migrate |
| **Total** | **35 tools** | |

### Tools Using BaseTool

| File | Tools Count | Status |
|------|-------------|--------|
| User-defined tools (dynamic) | Variable | ✅ Already using BaseTool |

## Migration Strategy

### Phase 1: Server Tools (Priority High) ✅
**Target:** `server_tools.py` - 11 tools
**Reason:** Most frequently used, core functionality
**Files to modify:**
- `server_tools.py` - Convert all tools to BaseTool classes
- Update imports and exports

**Tools:**
1. search_context
2. get_run_memory
3. query_structured_data
4. list_workspace_files
5. read_file_content
6. search_files
7. get_current_time
8. get_workspace_info
9. get_run_history
10. cache_get
11. cache_set

### Phase 2: Browser Tools (Priority Medium)
**Target:** `browser_tools.py` - 14 tools
**Reason:** Used for web automation
**Files to modify:**
- `browser_tools.py` - Convert all tools to BaseTool classes

**Tools:**
1. browser_launch
2. browser_goto
3. browser_click
4. browser_type
5. browser_press
6. browser_wait_for
7. browser_sleep
8. browser_scroll
9. browser_move_mouse
10. browser_screenshot
11. browser_get_text
12. browser_get_html
13. browser_close
14. (1 more)

### Phase 3: Example Tools (Priority Low)
**Target:** `example_tools.py` - 10 tools
**Reason:** Example/demo tools, less critical
**Decision:** May deprecate or keep as examples

## Technical Approach

### Step 1: Create Tool Class Template

```python
from aiwen.services.tools.base_tool import (
    BaseTool,
    ToolExecutionMode,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
from pydantic import Field

class ToolNameTool(BaseTool):
    """Tool description"""

    METADATA = ToolMetadata(
        name="tool_name",
        display_name="Tool Name",
        description="What this tool does",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="category_name",
        tags=["tag1", "tag2"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        param1: str = Field(description="Parameter description")
        param2: int = Field(default=10, description="Optional parameter")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        # Implementation
        result = await do_something(input_data.param1, input_data.param2)

        return ToolOutputSchema(
            success=True,
            message="Operation completed",
            data={"result": result}
        )
```

### Step 2: Update Imports

**Before:**
```python
from langchain_core.tools import tool
from aiwen.services.tools.execution_mode import server_tool

@tool("tool_name")
@server_tool(timeout=30)
async def tool_function(param1: str, param2: int = 10) -> dict:
    # ...
    return {"result": value}
```

**After:**
```python
from aiwen.services.tools.base_tool import (
    BaseTool,
    ToolExecutionMode,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
from pydantic import Field

class ToolNameTool(BaseTool):
    # ... (as shown above)
```

### Step 3: Update Tool Collections

**Before:**
```python
CONTEXT_TOOLS = [
    search_context,  # Function reference
    get_run_memory,
    query_structured_data,
]
```

**After:**
```python
CONTEXT_TOOLS = [
    SearchContextTool,  # Class reference
    GetRunMemoryTool,
    QueryStructuredDataTool,
]
```

### Step 4: Update Tool Loading in concrete.py

**Current approach** (using LangChain StructuredTool):
```python
from langchain_core.tools import StructuredTool

lc_tool = StructuredTool(
    name=metadata.name,
    description=metadata.description,
    coroutine=tool_instance.__call__,
    args_schema=tool_class.InputSchema,
)
```

**New approach** (direct BaseTool usage):
```python
# BaseTool instances can be used directly
# No need for LangChain wrapper
tool_instance = tool_class()
```

### Step 5: Update ToolRegistry

Ensure ToolRegistry can handle:
1. BaseTool class registration
2. Tool instance creation
3. Schema generation (already supports this)

## Benefits of Migration

### 1. Consistency
- All tools use the same base class
- Uniform interface and behavior
- Easier to understand and maintain

### 2. Type Safety
- Pydantic validation for inputs/outputs
- Better IDE support and autocomplete
- Catch errors at validation time

### 3. Extensibility
- Easy to add hooks (before_execute, after_execute, on_error)
- Consistent error handling
- Lifecycle management

### 4. Better Control
- No dependency on LangChain for tool definition
- Direct control over execution flow
- Easier testing and mocking

### 5. Feature Parity
- Supports all 5 execution modes
- HTTP, SERVER_RUN, CLIENT_RUN, CONTAINER_RUN, CELERY_RUN
- Unified configuration system

## Migration Checklist

### Phase 1: Server Tools
- [ ] Convert search_context
- [ ] Convert get_run_memory
- [ ] Convert query_structured_data
- [ ] Convert list_workspace_files
- [ ] Convert read_file_content
- [ ] Convert search_files
- [ ] Convert get_current_time
- [ ] Convert get_workspace_info
- [ ] Convert get_run_history
- [ ] Convert cache_get
- [ ] Convert cache_set
- [ ] Update CONTEXT_TOOLS, FILE_TOOLS, UTILITY_TOOLS exports
- [ ] Update server_tools.py imports
- [ ] Test all converted tools
- [ ] Update documentation

### Phase 2: Browser Tools
- [ ] Convert all 14 browser tools
- [ ] Update BROWSER_TOOLS export
- [ ] Test browser tool functionality
- [ ] Update documentation

### Phase 3: Integration
- [ ] Update concrete.py to use BaseTool directly
- [ ] Remove LangChain StructuredTool wrapper
- [ ] Update ToolRegistry if needed
- [ ] Integration testing
- [ ] Update all examples
- [ ] Update documentation

### Phase 4: Cleanup
- [ ] Remove unused LangChain imports
- [ ] Remove @server_tool decorator (if no longer needed)
- [ ] Remove example_tools.py or update it
- [ ] Clean up execution_mode.py decorators
- [ ] Final documentation update

## Testing Strategy

### Unit Tests
```python
async def test_tool_execution():
    tool = SearchContextTool()
    result = await tool(pattern="test", max_results=5)
    assert result["success"] is True
    assert "matches" in result["data"]
```

### Integration Tests
```python
async def test_tool_in_agent():
    config = {"server_tools": ["context"]}
    agent = DefaultAgentTemplate(config)
    message = UserMessage(message="Search for 'error' in context")
    result = await agent.run(message)
    # Verify tool was called and result is correct
```

### Performance Tests
- Measure execution time before and after
- Ensure no performance regression
- Monitor memory usage

## Rollback Plan

If migration causes issues:
1. Keep old @tool decorated functions in separate file
2. Add feature flag to switch between old/new tools
3. Gradual rollout with monitoring
4. Quick rollback mechanism

## Timeline

- **Week 1**: Phase 1 - Server Tools (11 tools)
- **Week 2**: Phase 2 - Browser Tools (14 tools)
- **Week 3**: Phase 3 - Integration & Testing
- **Week 4**: Phase 4 - Cleanup & Documentation

## Success Criteria

- ✅ All tools converted to BaseTool
- ✅ All tests passing
- ✅ No functionality regression
- ✅ Documentation updated
- ✅ Examples working
- ✅ Zero LangChain @tool dependencies

## Notes

- Keep backward compatibility during migration
- Document breaking changes clearly
- Provide migration guide for custom tools
- Update CI/CD pipeline if needed
