# Context Path Constants

## Overview

This document describes the context path constants used in the conflict executor prompts to avoid spelling errors and improve maintainability.

## Path Constants

All context path suffixes are defined in `ContextPathSuffix` class:

```python
from structure.plugins.executors.conflict.prompts import ContextPathSuffix

# Available constants
ContextPathSuffix.ALL_RUNS  # "all_runs"
ContextPathSuffix.JOINED_USERS  # "joined_users"
ContextPathSuffix.OWNER  # "owner"
ContextPathSuffix.WORKSPACE_HISTORY  # "workspace_history"
ContextPathSuffix.KNOWLEDGE  # "knowledge"
ContextPathSuffix.TOOLS  # "tools"
ContextPathSuffix.SKILLS  # "skills"
ContextPathSuffix.USER_HISTORY  # "history"
```

## Helper Functions

### `build_context_path(workspace_id, suffix)`

Build a complete context path from workspace ID and suffix.

```python
from structure.plugins.executors.conflict.prompts import (
    build_context_path,
    ContextPathSuffix,
)

# Build a single path
tools_path = build_context_path("ws_123", ContextPathSuffix.TOOLS)
# Result: "/ws_123/tools"
```

### `get_all_context_paths(workspace_id)`

Get all context paths for a workspace at once.

```python
from structure.plugins.executors.conflict.prompts import get_all_context_paths

# Get all paths as a dictionary
paths = get_all_context_paths("ws_123")

# Access individual paths
print(paths["tools"])  # "/ws_123/tools"
print(paths["skills"])  # "/ws_123/skills"
print(paths["workspace_history"])  # "/ws_123/workspace_history"
```

## Usage in Templates

The constants are automatically passed to templates by the render functions:

```python
from structure.plugins.executors.conflict.prompts import (
    render_system_prompt,
    render_available_context,
)

# System prompt includes all path constants
system_prompt = render_system_prompt(
    workspace_id="ws_123",
    core_tools="Tool descriptions..."
)

# Available context section
context_section = render_available_context(workspace_id="ws_123")
```

## Template Variables

When rendering templates, the following variables are available:

- `path_all_runs` - All runs path suffix
- `path_joined_users` - Joined users path suffix
- `path_owner` - Owner path suffix
- `path_workspace_history` - Workspace history path suffix
- `path_knowledge` - Knowledge base path suffix
- `path_tools` - Tools path suffix
- `path_skills` - Skills path suffix
- `path_user_history` - User history path suffix

Example in Jinja2 template:
```jinja2
cpath:/{{ workspace_id }}/{{ path_tools }}
```

## Benefits

1. **Type Safety**: Use constants instead of string literals
2. **Avoid Typos**: Compiler will catch misspelled constant names
3. **Easy Refactoring**: Change path suffix in one place
4. **Consistency**: Ensures all paths use the same suffixes
5. **Documentation**: Self-documenting code with clear constant names

## Migration Guide

### Before (String Literals)

```python
# ❌ Error-prone string literals
tools_path = f"/{workspace_id}/tools"
skills_path = f"/{workspace_id}/skilss"  # Typo!
```

### After (Constants)

```python
# ✅ Type-safe constants
from structure.plugins.executors.conflict.prompts import (
    build_context_path,
    ContextPathSuffix,
)

tools_path = build_context_path(workspace_id, ContextPathSuffix.TOOLS)
skills_path = build_context_path(workspace_id, ContextPathSuffix.SKILLS)
# Typo in constant name will be caught by IDE/linter
```

## Testing

All constants and helper functions are tested:

```bash
uv run python -c "
from aiwen.plugins.executors.conflict.prompts import (
    ContextPathSuffix,
    build_context_path,
    get_all_context_paths,
)

# Test path building
assert build_context_path('ws_1', ContextPathSuffix.TOOLS) == '/ws_1/tools'

# Test all paths
paths = get_all_context_paths('ws_1')
assert len(paths) == 8
assert paths['tools'] == '/ws_1/tools'
"
```
