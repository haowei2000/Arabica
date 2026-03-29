# Prompt Class Usage Guide

## Overview

The `Prompt` class provides a simple, type-safe way to create and render prompt templates with variable substitution.

## Basic Usage

### 1. Simple Prompt

```python
from structure.utils.prompt import Prompt

# Create a prompt template
greeting = Prompt("Hello, {name}! Welcome to {place}.")

# Render with variables
result = greeting.render(name="Alice", place="Wonderland")
# Output: "Hello, Alice! Welcome to Wonderland."
```

### 2. Safe Rendering (Missing Variables)

By default, missing variables are left as-is:

```python
prompt = Prompt("Name: {name}, Age: {age}, City: {city}")

# Missing 'city' variable
result = prompt.render(name="Bob", age=30)
# Output: "Name: Bob, Age: 30, City: {city}"
```

### 3. Strict Rendering (Raise on Missing)

```python
prompt = Prompt("Name: {name}, Age: {age}", strict=True)

# This will raise KeyError because 'age' is missing
result = prompt.render(name="Charlie")  # ❌ KeyError
```

## Advanced Features

### Template Concatenation

```python
from structure.utils.prompt import Prompt

base = Prompt("You are a helpful assistant.")
task = Prompt("\nTask: {task}")
context = Prompt("\nContext: {context}")

# Combine prompts
full_prompt = base + task + context

result = full_prompt.render(
    task="Summarize the document",
    context="User prefers concise answers"
)
```

### SystemPrompt with Defaults

```python
from structure.utils.prompt import SystemPrompt

# Use default template
system = SystemPrompt()
result = system.render(date="2024-01-01", time="10:00")

# Or provide custom template
custom_system = SystemPrompt("""
You are a code assistant.
Language: {language}
Style: {style}
""")
result = custom_system.render(language="Python", style="PEP 8")
```

### Load from File

```python
from structure.utils.prompt import Prompt

# Load template from file
prompt = Prompt.from_file("prompts/system_prompt.txt")
result = prompt.render(workspace_id="ws_123", tools="read, write")
```

### Quick Rendering Utility

```python
from structure.utils.prompt import render_prompt

# One-off rendering without creating Prompt object
result = render_prompt(
    "User {user} requested {action}",
    user="admin",
    action="delete"
)
```

## Real-World Example: Executor Prompts

### Define Prompt Templates

```python
# prompts.py
from structure.utils.prompt import Prompt, SystemPrompt


class MyExecutorPrompts:
    SYSTEM_PROMPT = SystemPrompt("""
You are an AI assistant in workspace {workspace_id}.

Available tools:
{tools}

Guidelines:
- Be concise
- Use tools when needed
""")

    CONTEXT_SECTION = Prompt("""
## Context

User preferences: {preferences}
History: {history}
""")

    @classmethod
    def build_full_prompt(cls, workspace_id: str, tools: str, **context):
        system = cls.SYSTEM_PROMPT.render(
            workspace_id=workspace_id,
            tools=tools
        )
        context_section = cls.CONTEXT_SECTION.render(**context)
        return system + "\n" + context_section
```

### Use in Executor

```python
# executor.py
from structure.core.interfaces import Executor
from .prompts import MyExecutorPrompts


class MyExecutor(Executor):
    def __init__(self, config):
        super().__init__(config)
        self.prompts = MyExecutorPrompts

    async def execute(self, run_context):
        # Build dynamic system prompt
        system_prompt = self.prompts.build_full_prompt(
            workspace_id=run_context.workspace_id,
            tools=self._format_tools(),
            preferences=run_context.user.preferences,
            history=self._get_history()
        )

        # Use system_prompt in LLM call
        response = await self.llm.chat(
            system=system_prompt,
            user_message=run_context.input
        )
        return response
```

## API Reference

### `Prompt`

| Method | Description |
|--------|-------------|
| `__init__(template, strict=False)` | Create prompt with template string |
| `render(**kwargs)` | Render template with variables (safe mode) |
| `render_strict(**kwargs)` | Render with strict validation (raise on missing) |
| `from_file(path, strict=False)` | Load template from file |
| `__add__(other)` | Concatenate prompts with `+` operator |

### `SystemPrompt`

Extends `Prompt` with a default system-level template.

| Method | Description |
|--------|-------------|
| `__init__(template=None, strict=False)` | Create with custom or default template |

### `render_prompt(template, **kwargs)`

Quick utility function for one-off rendering without creating a `Prompt` object.

## Best Practices

### ✅ DO

- Use `Prompt` for reusable templates
- Use `SystemPrompt` for system-level instructions
- Group related prompts in a class (e.g., `MyExecutorPrompts`)
- Use `strict=False` (default) when you want graceful degradation
- Use descriptive variable names in templates: `{workspace_id}` not `{id}`

### ❌ DON'T

- Don't use `strict=True` unless you're sure all variables will be provided
- Don't hardcode prompts directly in executor code—use prompt classes
- Don't mix f-strings and Prompt rendering (pick one approach)
- Don't forget to document what variables are required in your templates

## Migration Guide

### Before (Raw Strings)

```python
# Old approach
system_prompt_en = """
You are an AI assistant in workspace {{workspace_id}}.
Tools: {{tools}}
"""

# Usage
prompt = system_prompt_en.replace("{{workspace_id}}", ws_id).replace("{{tools}}", tools_str)
```

### After (Prompt Class)

```python
# New approach
from structure.utils.prompt import SystemPrompt

SYSTEM_PROMPT = SystemPrompt("""
You are an AI assistant in workspace {workspace_id}.
Tools: {tools}
""")

# Usage
prompt = SYSTEM_PROMPT.render(workspace_id=ws_id, tools=tools_str)
```

## Testing

```python
def test_prompt_rendering():
    p = Prompt("Hello {name}")
    assert p.render(name="World") == "Hello World"

    # Test missing variable (safe mode)
    p2 = Prompt("Name: {name}, Age: {age}")
    result = p2.render(name="Alice")
    assert result == "Name: Alice, Age: {age}"

    # Test strict mode
    p3 = Prompt("Value: {x}", strict=True)
    with pytest.raises(KeyError):
        p3.render()  # Missing 'x'
```
