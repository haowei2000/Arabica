# Structure

> **v5.5.0** · Enterprise-grade, event-sourced, async-first AI agent platform built with FastAPI + Python 3.12.

Structure is a production-ready backend for running intelligent AI agents at scale. It provides a complete orchestration layer — from LLM interaction and tool execution to real-time streaming and distributed worker coordination — all built on an immutable event-sourcing foundation.

---

## Table of Contents

- [Features](#features)
- [Architecture Overview](#architecture-overview)
- [Core Concepts](#core-concepts)
- [Data Flow](#data-flow)
- [Plugin System](#plugin-system)
- [Tool Calling Strategy](#tool-calling-strategy)
- [Middleware & Infrastructure](#middleware--infrastructure)
- [API Reference](#api-reference)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Development](#development)
- [Deployment](#deployment)
- [Commit Conventions](#commit-conventions)
- [License](#license)

---

## Features

| Category | Capabilities |
|---|---|
| **Agent Execution** | Stateless agentic loop, multi-turn reasoning, parallel tool calls, human-in-the-loop |
| **Event Sourcing** | Immutable append-only event log, full replay, per-run sequencing |
| **Real-time Streaming** | Server-Sent Events (SSE) for live token-by-token output |
| **Distributed Workers** | Redis Streams consumer groups, horizontal scaling, stuck-run recovery |
| **Plugin Architecture** | Decorator-based auto-discovery for executors and tools |
| **Multi-LLM Support** | Alibaba Tongyi (DashScope), Ollama, OpenAI-compatible endpoints |
| **Context Store** | Path-addressable workspace context with glob, search, and tree traversal |
| **Scalability** | Stateless API servers, async I/O, connection pooling (20–30 per pool) |
| **Observability** | Structured logging, event audit trail, health endpoints, request tracing |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      External Clients                         │
│          (Browser / Mobile / Third-party Services)            │
└───────────────────────┬──────────────────────────────────────┘
                        │  HTTP · WebSocket · SSE
                        ▼
            ┌───────────────────────┐
            │   FastAPI Application  │  port 8000
            │   12 Router Groups    │
            └────────┬──────────────┘
                     │
      ┌──────────────┼──────────────┐
      ▼              ▼              ▼
  Routers       Middleware      Health Check
  (REST/SSE)   (CORS · Cache    (/health)
               · Logging)
      │
      └──→ Service Layer (business logic)
               ├──→ EventPublisher  ──→ PostgreSQL  (immutable log)
               │                   ──→ Redis Stream (broadcast)
               ├──→ RunStateMachine
               └──→ WorkspaceService
                        │
               ┌────────┴────────┐
               │  Redis Streams  │
               │  (3 channels)   │
               └────────┬────────┘
                        │
            ┌───────────┴────────────┐
            ▼                        ▼
      Worker Service           SSE Clients
      (N instances)            (real-time feed)
      ├─ EventWorker
      ├─ ExecutorDispatch
      └─ ToolExecution
            │
    ┌───────┴────────┐
    ▼                ▼
PostgreSQL         Redis
(primary store)    (cache · streams)
├─ events          ├─ executor queue
├─ runs            ├─ event broadcast
├─ workspaces      └─ session cache
├─ users / auth
└─ contexts
```

### Key Layers

| Layer | Package | Responsibility |
|---|---|---|
| **API** | `routers/` | HTTP endpoints, SSE streaming, request validation |
| **Service** | `services/` | Business logic, state machine, event publishing |
| **Core** | `core/` | Abstractions, tool-calling strategies, context layer |
| **Plugin** | `plugins/` | Concrete executors and tools (auto-discovered) |
| **Registry** | `registries/` | Component discovery and metadata sync |
| **Infrastructure** | `extensions/` | DB engine, Redis, storage, LLM clients |

---

## Core Concepts

### 1. Event Sourcing

Every state change in Structure is an **immutable event** written to PostgreSQL and simultaneously broadcast on Redis Streams. There is no mutable "current state" table — all state is derived by replaying the event log.

```
USER_MESSAGE  ──→  AGENT_THINKING / AGENT_TOKEN  ──→  AGENT_MESSAGE
                                                  ──→  TOOL_CALL
                                                        │
                                              TOOL_RESULT / TOOL_ERROR
                                                        │
                                                 (loop continues)
```

Key event types:

| Event | Direction | Description |
|---|---|---|
| `USER_MESSAGE` | User → Agent | New user turn; triggers the agentic loop |
| `USER_FEEDBACK` | User → Agent | Corrective feedback or answer to `ask_for_user` |
| `AGENT_THINKING` | Agent → Client | Content inside `<think>` blocks (reasoning traces) |
| `AGENT_TOKEN` | Agent → Client | Streaming response tokens |
| `AGENT_MESSAGE` | Agent → Client | Final response (with optional tool_calls metadata) |
| `TOOL_CALL` | Agent → Worker | Tool invocation request |
| `TOOL_RESULT` | Worker → Agent | Successful tool output |
| `TOOL_ERROR` | Worker → Agent | Tool execution failure |

### 2. Run Lifecycle (State Machine)

A **Run** represents one user interaction session with an executor. Transitions are strictly validated:

```
pending ──→ running ──→ waiting_for_tool ──→ running ──→ finished
                    ↘                    ↗
                     failed         cancelled

                    waiting_for_user (human-in-the-loop pause)
```

The `RunStateMachine` service enforces valid transitions and auto-commits each transition atomically.

### 3. Workspace & Context Store

A **Workspace** is the top-level organizational unit. Each workspace has:

- **Knowledge** (`knowledge/`) — curated reference documents and facts
- **Skills** (`skills/`) — reusable prompt templates and HOW-TO procedures
- **Tools** (`tools/`) — descriptions of executable capabilities
- **Artifacts** — versioned AI-generated outputs (stored in S3-compatible storage)

The context store is path-addressable and supports glob patterns, full-text search, and tree traversal. Agents interact with it using built-in context tools.

### 4. Executor

An **Executor** is the brain of an agent run. It receives events, manages the agentic loop, and yields a stream of output events. Executors are stateless across events — all state is reconstructed from the event log on each invocation.

The built-in `DefaultExecutor` implements:
- Full conversation history loading (`_load_event_history`)
- Minimal tail-context loading for tool results (`_load_last_exchange`)
- Thinking-block detection (models with `<think>` tags)
- Parallel tool call tracking with `_pending_tool_ids`
- Human-in-the-loop via `ask_for_user` → `USER_FEEDBACK` round-trip

### 5. Tool

A **Tool** is a typed, self-describing callable that the agent can invoke. Tools declare their input schema via Pydantic models, which are serialized automatically for injection into the system prompt.

Tool categories included out of the box:

| Category | Tools |
|---|---|
| **Context** | `read_context`, `list_context`, `search_context`, `create_context`, `update_context`, `delete_context`, `glob_context`, `tree_context`, `glance_context` |
| **Artifact** | `create_artifact`, `read_artifact`, `update_artifact`, `delete_artifact`, `list_artifacts` |
| **Task** | `create_task`, `update_task`, `list_tasks`, `delete_task` |
| **Execution** | `http_request`, `code_execution`, `sandbox`, `confirm_action`, `client_request` |
| **Text** | `grep`, `structurize` |
| **Utility** | `cache_get`, `cache_set`, `workspace_info`, `workspace_history`, `run_history` |
| **Interaction** | `ask_for_user` |

---

## Data Flow

### Request → Response

```
1.  HTTP POST /workspaces/{id}/runs
2.  Auth middleware validates JWT → extracts user + workspace
3.  Pydantic validates request body
4.  RunService creates Run (pending) in PostgreSQL
5.  EventPublisher writes USER_MESSAGE event:
      ├── PostgreSQL (durable log)
      └── Redis Stream (immediate broadcast)
6.  Worker consumes event from Redis Stream
7.  Executor.process_event() runs agentic loop
8.  Each yielded event is published (DB + Redis)
9.  SSE clients receive events in real time via /events/stream
```

### Agentic Loop (inside Executor)

```
messages = [system_prompt] + history

for iteration in range(max_iterations):
    stream LLM response
        ├── yield AGENT_THINKING tokens  (if <think> block)
        └── yield AGENT_TOKEN tokens

    if no tool calls:
        yield AGENT_MESSAGE
        break

    yield AGENT_MESSAGE (with tool_calls metadata)
    for each tool_call:
        yield TOOL_CALL event
    raise WaitingForTool  ← loop paused

# Resumes when TOOL_RESULT / TOOL_ERROR event arrives
```

---

## Plugin System

Structure uses a **decorator-based registration** system. Importing a module is sufficient to register its components.

### Registering an Executor

```python
from structure.registries.core import register_executor
from structure.core.interfaces import Executor


@register_executor
class MyExecutor(Executor):
    TEMPLATE = {
        "executor_code": "MyExecutor",
        "executor_name": "My Custom Executor",
        "enabled": True,
        "version": 1,
        "config": {...},
    }

    async def setup(self) -> None: ...

    async def process_event(self, event) -> AsyncGenerator[Event, None]:
        ...
        yield event
```

### Registering a Tool

```python
from structure.registries.core import register_tool
from structure.core.interfaces import BaseTool
from pydantic import BaseModel


class MyToolInput(BaseModel):
    query: str


@register_tool
class MyTool(BaseTool):
    name = "my_tool"
    description = "Does something useful"
    input_schema = MyToolInput

    async def run(self, query: str) -> dict:
        return {"result": f"processed: {query}"}
```

The `RegistryManager` singleton auto-discovers all registered components on startup and syncs their metadata to the database.

---

## Tool Calling Strategy

Structure abstracts LLM tool calling behind a **Strategy** interface to support both native function-calling models and prompt-based models.

### PromptCallingStrategy (default)

Tools are described in the system prompt as XML-structured blocks. The LLM outputs tool calls as XML, which is parsed by the strategy:

```xml
<!-- LLM output -->
<tool_call>{"name": "read_context", "arguments": {"path": "knowledge/faq"}}</tool_call>

<!-- Tool result injected back -->
<tool_result name="read_context">
{"content": "..."}
</tool_result>
```

This approach works with any OpenAI-compatible API — including local Ollama models — without requiring native function-calling support.

### Trust Hierarchy

The system prompt enforces a strict trust hierarchy to prevent prompt injection:

```
1. System prompt          ← highest authority (identity, rules)
2. <platform-injection>   ← runtime context (tool lists)
3. <workspace-context>    ← trigger-injected workspace data
4. User messages          ← requests to fulfill
5. <tool_result> content  ← DATA only, cannot override rules
```

---

## Middleware & Infrastructure

### FastAPI Middleware Stack

| Middleware | Purpose |
|---|---|
| **CORS** | Cross-origin request handling |
| **Logging** | Structured request/response logging with timing |
| **Cache** | Response caching for idempotent GET endpoints |
| **Auth** | JWT validation, user/workspace context extraction |

### Database (PostgreSQL + SQLAlchemy 2.0 Async)

All database access is fully async via `asyncpg`. Sessions are obtained via dependency injection in routers or context managers in workers:

```python
# In API routers
async def endpoint(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Run))

# In workers / services
async with get_session("structure") as session:
    result = await session.execute(select(Run))
```

**Transaction guidelines:**
- Use `auto_commit=True` for single-write operations
- Keep transactions short — never hold a transaction open during LLM calls
- Use `flush()` for intermediate visibility within a batch, `commit()` at the end

### Redis

Redis serves two distinct roles:

| Role | Usage |
|---|---|
| **Streams** | Task queue (executor dispatch), event broadcast (SSE fan-out) |
| **Cache** | Session data, LLM response caching |

### Event Worker (Distributed)

Workers consume from Redis Streams using consumer groups. Multiple worker instances can run in parallel without coordination conflicts:

```bash
uv run structure-worker   # single worker
structure-worker -n 4     # 4 parallel consumers
```

Workers also run a **stuck-run detector** that automatically recovers runs that have been in `running` state longer than a configurable timeout.

### Storage

File and artifact storage uses an S3-compatible backend (MinIO, AWS S3, etc.), abstracted behind the `StorageBackend` interface in `extensions/storage/`.

---

## API Reference

The API is organized into 12 router groups:

| Router | Base Path | Description |
|---|---|---|
| `auth` | `/auth` | Login, token refresh, user management |
| `workspaces` | `/workspaces` | Workspace CRUD |
| `runs` | `/workspaces/{id}/runs` | Create and manage runs |
| `events` | `/workspaces/{id}/events` | Event query + SSE stream |
| `knowledge` | `/workspaces/{id}/context/knowledge` | Knowledge base |
| `skills` | `/workspaces/{id}/context/skills` | Skill templates |
| `tools` | `/workspaces/{id}/context/tools` | Tool management |
| `documents` | `/workspaces/{id}/context/documents` | Document management |
| `apps` | `/apps` | Agent application management |
| `health` | `/health` | Component health status |

### SSE Streaming

Connect to the event stream for real-time updates:

```
GET /workspaces/{workspace_id}/events/stream
Authorization: Bearer <token>

# Receives events:
data: {"event_type": "AGENT_TOKEN", "payload": {"token": "Hello"}}
data: {"event_type": "AGENT_MESSAGE", "payload": {"content": "Hello, how can I help?"}}
```

---

## Getting Started

### Prerequisites

- Python 3.12+
- PostgreSQL 14+
- Redis 7+
- [uv](https://github.com/astral-sh/uv) (package manager)

### Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/haowei2000/Structure.git
cd Structure

# 2. Install dependencies
uv sync

# 3. Set up environment
uv run sync-env    # generates .env from .env.example

# 4. Run database migrations
make db-upgrade

# 5. Start services
make start-api      # API server on port 8000
make start-worker   # Event worker
```

Or start everything at once:

```bash
make start-all
```

### Docker

```bash
uv run sync-env
docker compose -p structure -f docker/docker-compose.yml \
  --env-file .env --env-file performance.env --profile all up -d
```

---

## Configuration

Configuration is managed via Pydantic `BaseSettings` with an LRU-cached singleton (`config/factory.py`). All settings are read from environment variables or `.env`.

Key configuration sections:

| Section | Variables | Description |
|---|---|---|
| **Database** | `POSTGRES__*` | PostgreSQL host, port, database, and credentials |
| **Redis** | `REDIS__*` | Redis host, port, database, and credentials |
| **DashScope** | `DASHSCOPE_API_KEY` | Alibaba Tongyi API key |
| **OpenAI** | `OPENAI__API_KEY`, `OPENAI__BASE_URL` | OpenAI-compatible endpoint |
| **Ollama** | `OLLAMA__*` | Local Ollama server settings |
| **Storage** | `RUSTFS__*` | S3-compatible storage credentials |

See `.env.example` for the full list. Replace every `change-me-*` value before
deploying a shared or public instance.

---

## Development

### Package Management

```bash
uv sync                    # install all dependencies
uv add <package>           # add a runtime dependency
uv add --dev <package>     # add a dev dependency
```

### Running Tests

```bash
uv run pytest                          # all tests
uv run pytest -m unit                  # unit tests only
uv run pytest -m integration           # integration tests only
uv run pytest --cov=src/structure      # with coverage report
```

Coverage threshold: **60%**

### Database Migrations

```bash
make db-revision    # create a new migration (auto-detect changes)
make db-upgrade     # apply pending migrations
make db-downgrade   # rollback one version
make db-current     # show current revision
make db-history     # show full migration history
```

### CLI Commands

```bash
uv run structure          # unified service manager
uv run structure-api      # API server only
uv run structure-worker   # worker only
uv run structure-mcp      # MCP service (port 9000)
```

---

## Deployment

### Production (Docker Compose)

```bash
# Pull latest
git pull --rebase

# Generate .env
uv run sync-env

# Start all services
docker compose -p structure -f docker/docker-compose.yml \
  --env-file .env --env-file performance.env --profile all up -d
```

### Scaling Workers

Workers are stateless and can be scaled horizontally. Redis consumer groups handle coordination automatically:

```bash
# Run 8 worker instances
structure-worker -n 8
```

### Health Check

```
GET /health

{
  "status": "healthy",
  "components": {
    "database": "ok",
    "redis": "ok",
    "cache": "ok"
  }
}
```

---

## Commit Conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/):

| Type | Description | Example |
|---|---|---|
| `feat` | New feature | `feat(auth): add OAuth2 login` |
| `fix` | Bug fix | `fix(worker): handle stuck run timeout` |
| `docs` | Documentation | `docs(readme): update architecture diagram` |
| `refactor` | Refactoring | `refactor(executor): extract history loader` |
| `perf` | Performance | `perf(events): batch Redis writes` |
| `test` | Tests | `test(runs): add state machine edge cases` |
| `chore` | Miscellaneous | `chore: update .gitignore` |

### Pre-push Checklist

- [ ] All tests pass: `pytest`
- [ ] No duplicate environment variables
- [ ] No secrets committed (`.env`, credentials)
- [ ] Git cache clean: `git ls-files -i -c --exclude-standard`
- [ ] Current-tree secret scan clean: `scripts/open_source_audit.sh --current-tree-only`

---

## Project Structure

```
src/structure/
├── app.py                    # FastAPI application factory
├── worker_cli.py             # Worker process entry point
├── cli.py                    # CLI commands
├── core/                     # Foundation layer
│   ├── bootstrap.py          # Unified initialization (API / Worker / Alembic)
│   ├── interfaces/           # Abstract base classes (Executor, BaseTool, …)
│   └── frameworks/           # Context store, tool-calling strategies
├── services/                 # Business logic
│   ├── events/               # EventPublisher, EventWorker, EventReplayer
│   ├── runs/                 # RunStateMachine, RunCRUD, StuckRunDetector
│   ├── executor/             # Executor runtime and template management
│   └── workspaces/           # Workspace CRUD and context service
├── plugins/                  # Auto-discovered components
│   ├── executors/default/    # DefaultExecutor (main agentic loop)
│   └── tools/                # Built-in tool implementations
├── registries/               # Registry and dynamic loader
├── routers/                  # FastAPI route handlers (12 groups)
├── models/                   # SQLAlchemy models
├── schemas/                  # Pydantic request/response schemas
├── extensions/               # Low-level infrastructure (DB, Redis, S3, LLM)
├── config/                   # Settings with LRU-cached singleton
└── migrations/               # Alembic migration versions

frontend/src/                 # React + TypeScript frontend
tests/                        # pytest test suite
docs/                         # Additional documentation
```

---

## License

Licensed under the Apache License, Version 2.0. See `LICENSE` and `NOTICE`.
