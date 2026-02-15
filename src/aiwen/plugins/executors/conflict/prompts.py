"""Prompt templates for the conflict executor using Jinja2."""
from aiwen.schemas.context import ContextSchema
from aiwen.utils.prompt import get_jinja_env

# Get a shared Jinja2 environment for all templates
_env = get_jinja_env(strict=False)


# ──────────────────────────────────────────────────────────────
# Context Path Constants
# ──────────────────────────────────────────────────────────────

class ContextPathSuffix:
    """Context path suffix constants to avoid spelling errors."""

    ALL_RUNS = "all_runs"
    JOINED_USERS = "joined_users"
    OWNER = "owner"
    WORKSPACE_HISTORY = "workspace_history"
    KNOWLEDGE = "knowledge"
    TOOLS = "tools"
    SKILLS = "skills"
    USER_HISTORY = "history"


# Convenience aliases for template usage
PATH_ALL_RUNS = ContextPathSuffix.ALL_RUNS
PATH_JOINED_USERS = ContextPathSuffix.JOINED_USERS
PATH_OWNER = ContextPathSuffix.OWNER
PATH_WORKSPACE_HISTORY = ContextPathSuffix.WORKSPACE_HISTORY
PATH_KNOWLEDGE = ContextPathSuffix.KNOWLEDGE
PATH_TOOLS = ContextPathSuffix.TOOLS
PATH_SKILLS = ContextPathSuffix.SKILLS
PATH_USER_HISTORY = ContextPathSuffix.USER_HISTORY


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
|—— all_runs(cpath:/{{ workspace_id }}/{{ path_all_runs }})
├── joined_users (cpath: /{{ workspace_id }}/{{ path_joined_users }})
│—— owner: (cpath:/{{ workspace_id }}/{{ path_owner }})
└── available_context
    ├── workspace_history(cpath:/{{ workspace_id }}/{{ path_workspace_history }})  — Past run records
    ├── available_knowledge(cpath:/{{ workspace_id }}/{{ path_knowledge }}) — User-uploaded knowledge bases
    ├── available_tools(cpath:/{{ workspace_id }}/{{ path_tools }})     — Available tools
    ├── available_skills(cpath:/{{ workspace_id }}/{{ path_skills }})    — Available skills
    └── user_history (cpath:/{{ workspace_id }}/{{ path_user_history }})       — User's global activity history
```

## Core Tools
{{ core_tools }}
""")


# ──────────────────────────────────────────────────────────────
# Context Resources
# ──────────────────────────────────────────────────────────────

AVAILABLE_CONTEXT = _env.from_string("""
## Available Context Resources

The following context resources are accessible in the current workspace. Query them using core tools (read, list, search, etc.) with the corresponding cpath:

1. **Workspace History** (cpath: "/{{ workspace_id }}/{{ path_workspace_history }}") — Past run records including tool calls and results. Use to understand previous actions.
2. **Knowledge Bases** (cpath: "/{{ workspace_id }}/{{ path_knowledge }}") — Documents, links, and reference materials uploaded by the user. Use to find domain-specific information.
3. **Tools** (cpath: "/{{ workspace_id }}/{{ path_tools }}") — Available tools in this workspace and their usage instructions.
4. **Skills** (cpath: "/{{ workspace_id }}/{{ path_skills }}") — Available skills in this workspace and their usage instructions.
5. **User History** (cpath: "/{{ workspace_id }}/{{ path_user_history }}") — The user's activity history across the entire system. Use to understand preferences and past needs.
""")

WORKSPACE_HISTORY = _env.from_string("""
## Workspace Run History

Below are past Run records for this workspace. Each record includes Run ID, status, start/end time, tool calls, and results:

{% for run in runs %}
- **Run #{{ loop.index }}**: {{ run.path }}
  - Status: {{ run.status }}
  - Started: {{ run.started_at }}
  {% if run.completed_at %}
  - Completed: {{ run.completed_at }}
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

- View tool details: Use `read(cpath)` to get the description and parameter definitions
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
# Path Construction Helpers
# ──────────────────────────────────────────────────────────────

def build_context_path(workspace_id: str, suffix: str) -> str:
    """Build a complete context path from workspace ID and suffix.

    Args:
        workspace_id: The workspace ID
        suffix: The context path suffix (use ContextPathSuffix constants)

    Returns:
        Complete context path in format: /{workspace_id}/{suffix}

    Example:
        >>> build_context_path("ws_123", ContextPathSuffix.TOOLS)
        '/ws_123/tools'
    """
    return f"/{workspace_id}/{suffix}"


def get_all_context_paths(workspace_id: str) -> dict[str, str]:
    """Get all context paths for a workspace.

    Args:
        workspace_id: The workspace ID

    Returns:
        Dictionary mapping context names to their full paths

    Example:
        >>> paths = get_all_context_paths("ws_123")
        >>> paths["tools"]
        '/ws_123/tools'
    """
    return {
        "all_runs": build_context_path(workspace_id, PATH_ALL_RUNS),
        "joined_users": build_context_path(workspace_id, PATH_JOINED_USERS),
        "owner": build_context_path(workspace_id, PATH_OWNER),
        "workspace_history": build_context_path(workspace_id, PATH_WORKSPACE_HISTORY),
        "knowledge": build_context_path(workspace_id, PATH_KNOWLEDGE),
        "tools": build_context_path(workspace_id, PATH_TOOLS),
        "skills": build_context_path(workspace_id, PATH_SKILLS),
        "user_history": build_context_path(workspace_id, PATH_USER_HISTORY),
    }


# ──────────────────────────────────────────────────────────────
# Utility Functions
# ──────────────────────────────────────────────────────────────

def render_system_prompt(workspace_id: str, core_tools: str, **extra: str) -> str:
    """Render the system prompt with workspace context.

    Args:
        workspace_id: The workspace ID
        core_tools: Description of core tools available
        **extra: Additional template variables

    Returns:
        Rendered system prompt string
    """
    return SYSTEM_PROMPT.render(
        workspace_id=workspace_id,
        core_tools=core_tools,
        # Context path constants
        path_all_runs=PATH_ALL_RUNS,
        path_joined_users=PATH_JOINED_USERS,
        path_owner=PATH_OWNER,
        path_workspace_history=PATH_WORKSPACE_HISTORY,
        path_knowledge=PATH_KNOWLEDGE,
        path_tools=PATH_TOOLS,
        path_skills=PATH_SKILLS,
        path_user_history=PATH_USER_HISTORY,
        **extra,
    )


def render_available_context(workspace_id: str) -> str:
    """Render the available context resources section.

    Args:
        workspace_id: The workspace ID

    Returns:
        Rendered available context string
    """
    return AVAILABLE_CONTEXT.render(
        workspace_id=workspace_id,
        # Context path constants
        path_workspace_history=PATH_WORKSPACE_HISTORY,
        path_knowledge=PATH_KNOWLEDGE,
        path_tools=PATH_TOOLS,
        path_skills=PATH_SKILLS,
        path_user_history=PATH_USER_HISTORY,
    )


def render_workspace_history(runs: list[ContextSchema]) -> str:
    """Render the workspace history section.

    Args:
        runs: List of run context schemas

    Returns:
        Rendered workspace history string
    """
    return WORKSPACE_HISTORY.render(runs=runs)


def render_user_history(user_history: list[ContextSchema]) -> str:
    """Render the user history section.

    Args:
        user_history: List of user history context schemas

    Returns:
        Rendered user history string
    """
    return USER_HISTORY.render(user_history=user_history)


def render_available_knowledge(knowledge_list: list[ContextSchema]) -> str:
    """Render the knowledge bases section.

    Args:
        knowledge_list: List of knowledge base context schemas

    Returns:
        Rendered knowledge bases string
    """
    return AVAILABLE_KNOWLEDGE.render(knowledge_list=knowledge_list)


def render_available_tools(tools: list[ContextSchema]) -> str:
    """Render the available tools section.

    Args:
        tools: List of tool context schemas

    Returns:
        Rendered available tools string
    """
    return AVAILABLE_TOOLS.render(tools=tools)


def render_available_skills(skills: list[ContextSchema]) -> str:
    """Render the available skills section.

    Args:
        skills: List of skill context schemas

    Returns:
        Rendered available skills string
    """
    return AVAILABLE_SKILLS.render(skills=skills)
