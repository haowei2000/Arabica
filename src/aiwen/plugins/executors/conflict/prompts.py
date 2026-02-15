"""Prompt templates for the conflict executor using Jinja2."""

from jinja2 import Template

from aiwen.utils.prompt import get_jinja_env

# Get a shared Jinja2 environment for all templates
_env = get_jinja_env(strict=False)


# ──────────────────────────────────────────────────────────────
# System Prompt
# ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = _env.from_string("""
You are an AI assistant operating within a Workspace. When a user sends a request, the system creates a Run inside the workspace to execute the task. The workspace contains tools, knowledge bases, and other resources provided by the user. Your job is to leverage these resources effectively to help the user.

## Behavioral Guidelines

- Understand the user's intent before deciding whether to invoke tools. If you already have enough information, answer directly.
- When querying resources, prefer search() for targeted retrieval over broad list() calls.
- Keep responses concise, accurate, and well-structured. For multi-step tasks, explain each step clearly.
- If a tool call fails, analyze the cause and try alternatives rather than blindly retrying.
- If you cannot fulfill a request, explain why honestly and suggest possible alternatives.

## Current Workspace

```
Workspace
|—— all_runs(cpath:/{{ workspace_id }}/all_runs)
├── joined_users (cpath: /{{ workspace_id }}/joined_users)
│—— owner: (cpath:/{{ workspace_id }}/owner)
└── available_context
    ├── workspace_history(cpath:/{{ workspace_id }}/workspace_history)  — Past run records
    ├── available_knowledge(cpath:/{{ workspace_id }}/knowledge}) — User-uploaded knowledge bases
    ├── available_tools(cpath:/{{ workspace_id }}/tools)     — Available tools
    ├── available_skills(cpath:/{{ workspace_id }}/skills)    — Available skills
    └── user_history (cpath:/{{ workspace_id }}/history)       — User's global activity history
```

## Core Tools
{{ core_tools }}
""")


# ──────────────────────────────────────────────────────────────
# Context Resources
# ──────────────────────────────────────────────────────────────

AVAILABLE_CONTEXT = _env.from_string("""
## Available Context Resources

The following context resources are accessible in the current workspace. Query them using core tools (read, list, search, etc.) with the corresponding ID:

1. **Workspace History** (id: "workspace_history") — Past run records including tool calls and results. Use to understand previous actions.
2. **Knowledge Bases** (id: "available_knowledge") — Documents, links, and reference materials uploaded by the user. Use to find domain-specific information.
3. **Tools** (id: "available_tools") — Available tools in this workspace and their usage instructions.
4. **Skills** (id: "available_skills") — Available skills in this workspace and their usage instructions.
5. **User History** (id: "user_history") — The user's activity history across the entire system. Use to understand preferences and past needs.
""")

WORKSPACE_HISTORY = _env.from_string("""
## Workspace Run History

Below are past Run records for this workspace. Each record includes Run ID, status, start/end time, tool calls, and results:

{% for run in runs %}
- **Run #{{ loop.index }}**: {{ run.id }}
  - Status: {{ run.status }}
  - Started: {{ run.started_at }}
  {% if run.completed_at %}
  - Completed: {{ run.completed_at }}
  {% endif %}
  {% if run.tools %}
  - Tools used: {{ run.tools|join(', ') }}
  {% endif %}
{% endfor %}
""")

USER_HISTORY = _env.from_string("""
## User History

Below is this user's activity history across the system. Each record includes timestamp, user input, system response, and tool calls:

{% for entry in user_history %}
- **{{ entry.timestamp }}**: {{ entry.user_input }}
  {% if entry.response %}
  - Response: {{ entry.response }}
  {% endif %}
  {% if entry.tools %}
  - Tools: {{ entry.tools|join(', ') }}
  {% endif %}
{% endfor %}
""")

AVAILABLE_KNOWLEDGE = _env.from_string("""
## Knowledge Bases

Below are the knowledge base resources uploaded to this workspace. Each entry includes an ID, name, and type (document, link, etc.). Use `read(id)` to view content or `search(id, query)` to retrieve information:

{% for kb in knowledge_list %}
- **{{ kb.name }}** (id: `{{ kb.id }}`)
  - Type: {{ kb.type }}
  {% if kb.description %}
  - Description: {{ kb.description }}
  {% endif %}
{% endfor %}
""")

AVAILABLE_TOOLS = _env.from_string("""
## Available Tools

Below are the tools registered in this workspace. Tools include core tools and custom tools. Each tool has an ID, name, description, and input/output format.

- View tool details: Use `read(tool_id)` to get the description and parameter definitions
- Execute a tool: Use `run(tool_id, params)` to invoke it and get results

{% for tool in tools %}
- **{{ tool.name }}** (`{{ tool.id }}`)
  {% if tool.description %}
  - {{ tool.description }}
  {% endif %}
  {% if tool.parameters %}
  - Parameters: {{ tool.parameters|join(', ') }}
  {% endif %}
{% endfor %}
""")

AVAILABLE_SKILLS = _env.from_string("""
## Available Skills

Below are the skills registered in this workspace. Each skill has an ID, name, description, and input/output format:

{% for skill in skills %}
- **{{ skill.name }}** (`{{ skill.id }}`)
  {% if skill.description %}
  - {{ skill.description }}
  {% endif %}
{% endfor %}

- View skill details: Use `read(skill_id)`, `list(skill_id)`, or `search(skill_id, query)` to explore skill content
- Execute a skill: Use `run(skill_id, params)` with the skill ID and input parameters
""")


# ──────────────────────────────────────────────────────────────
# Utility Functions
# ──────────────────────────────────────────────────────────────

def render_system_prompt(workspace_id: str, core_tools: str, **extra: str) -> str:
    """Render the system prompt with workspace context."""
    return SYSTEM_PROMPT.render(
        workspace_id=workspace_id,
        core_tools=core_tools,
        **extra,
    )


def render_workspace_history(runs: list[dict]) -> str:
    """Render the workspace history section."""
    return WORKSPACE_HISTORY.render(runs=runs)


def render_user_history(user_history: list[dict]) -> str:
    """Render the user history section."""
    return USER_HISTORY.render(user_history=user_history)


def render_available_knowledge(knowledge_list: list[dict]) -> str:
    """Render the knowledge bases section."""
    return AVAILABLE_KNOWLEDGE.render(knowledge_list=knowledge_list)


def render_available_tools(tools: list[dict]) -> str:
    """Render the available tools section."""
    return AVAILABLE_TOOLS.render(tools=tools)


def render_available_skills(skills: list[dict]) -> str:
    """Render the available skills section."""
    return AVAILABLE_SKILLS.render(skills=skills)
