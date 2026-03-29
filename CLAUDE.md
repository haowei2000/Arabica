# CLAUDE.md
Dont run format check and other unnecessary oprerations.
Please Write the code in English.
Import: please hint me to fix the grammar error when i send a message to help to improve my English  
if I ask with Chinese, please translate it to English
## Project Overview

**Aiwen Service (v5.5.0)** - An enterprise-grade AI-powered agent orchestration platform with event-sourced architecture.

### Key Characteristics
- 🎯 **Event-Sourced Architecture**: All state changes captured as immutable events
- ⚡ **Async-First Design**: Built on FastAPI + AsyncIO for high concurrency
- 🔌 **Plugin-Based Extensibility**: Dynamic executor and tool discovery via registry system
- 📊 **Real-time Streaming**: Server-Sent Events (SSE) for live updates
- 🔄 **Distributed Workers**: Horizontal scaling with Redis Streams
- 🧩 **Multi-LLM Support**: OpenAI, DashScope, Ollama integration

---

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    External Clients                          │
│         (Frontend, Mobile, Third-party Services)             │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP / WebSocket / SSE
                     ▼
         ┌───────────────────────────┐
         │   FastAPI Application     │
         │   (Port 8000)             │
         └─────────┬─────────────────┘
                   │
    ┌──────────────┼──────────────┐
    │              │              │
    ▼              ▼              ▼
Routers      Middleware      Health Check
(12 modules)  (CORS, Cache,   (Health Status)
              Logging)
    │
    ├─→ Request Processing Pipeline
    │   └─→ Service Layer (Business Logic)
    │       ├─→ Event Publishing
    │       ├─→ State Machine Management
    │       └─→ Data Persistence
    │
    └─→ Redis Streams
        ├─→ Executor Tasks
        ├─→ Run Events
        └─→ Workspace Events
                     │
         ┌───────────┴────────────┐
         │                        │
         ▼                        ▼
    PostgreSQL            Redis Cache/Streams
    (Primary Storage)     (Real-time Messaging)
    ├─ Events             ├─ Executor queue
    ├─ Runs               ├─ Event broadcast
    ├─ Workspaces         └─ Session cache
    ├─ Users/Auth
    └─ Contexts
         │                        │
         └────────────┬───────────┘
                      │
                      ▼
            ┌──────────────────────┐
            │  Worker Service      │
            │ (Multiple instances) │
            │ - Event Consumption  │
            │ - Executor Dispatch  │
            │ - Tool Execution     │
            │ - Event Publishing   │
            └──────────────────────┘
```

### Core Components

#### 1. **Bootstrap System** (`core/bootstrap.py`)
Unified initialization system with pluggable configuration:
- **API Bootstrap**: Full initialization (logging, DB, Redis, registries, storage)
- **Worker Bootstrap**: Worker-specific init (no user creation)
- **Alembic Bootstrap**: Migration-only init (DB only)
- **Celery Bootstrap**: Task worker initialization

#### 2. **Event-Sourcing Layer** (`services/events/`)
All state changes captured as immutable events:
- **EventPublisher**: Dual-write to PostgreSQL + Redis Streams
- **EventWorker**: Redis Stream consumer for executor dispatch
- **EventConsumer/Replayer**: Event stream processing and replay
- **Auto-sequencing**: Per run and workspace event ordering

#### 3. **Registry & Plugin System** (`registries/`)
Dynamic component discovery and instantiation:
- **ExecutorRegistry**: Manages executor templates
- **ToolRegistry**: Manages tool classes
- **Dynamic Loading**: Auto-discovery via `@register_tool` / `@register_executor`
- **Database Sync**: Automatic metadata synchronization

#### 4. **Tool Calling Architecture** (`core/tool_calling/`)
Strategy-based tool invocation abstraction:
- **FunctionCallingStrategy**: OpenAI native function calling
- **PromptCallingStrategy**: XML-based prompt engineering for non-native models
- **Unified Response**: `LLMResponse` with parsed tool calls

#### 5. **State Machine** (`services/runs/`)
Run lifecycle management with strict transitions:
```
pending → running → waiting_for_tool → running → finished
                 ↘              ↘
                  failed        cancelled
```

#### 6. **Context Layer** (`core/frameworks/context_layer.py`)
Advanced context management with path-addressable nodes:
- **ContextStore**: In-memory context with glob pattern support
- **DetailLevel**: Multi-level information disclosure (GLANCE/OVERVIEW/DETAIL)
- **Persistent Storage**: Database-backed context per workspace

#### 7. **Distributed Worker System** (`worker_cli.py`)
Horizontal scaling with Redis consumer groups:
```bash
structure-worker -n 4  # Start 4 parallel workers
```
- Consumer group coordination
- Stuck run detection and recovery
- Graceful shutdown handling

### Data Flow

#### Request → Response Flow
```
1. HTTP Request → FastAPI Router
2. Auth Middleware → Extract user/workspace context
3. Pydantic Validation → Parse request body
4. Service Layer → Business logic
5. EventPublisher → Dual-write (PostgreSQL + Redis)
6. Response → Client
   ├─ SSE Clients: Real-time via /stream endpoint
   └─ Workers: Consume from Redis Stream
```

#### Agent Execution Flow
```
1. User message → Create Run (pending state)
2. Publish "user.message" event → Redis Stream
3. Worker picks up task → Load Executor from registry
4. Executor.run() → Stream events:
   ├─ Tool discovery (ToolRegistry)
   ├─ Tool execution (ToolCaller)
   ├─ LLM interaction (ToolCallingStrategy)
   └─ Event emission (AgentEvent)
5. EventPublisher → Broadcast all events
6. RunStateMachine → Transition to finished/failed
```

### Key Design Patterns

| Pattern | Location | Purpose |
|---------|----------|---------|
| **Factory** | `registries/core.py` | Component instantiation |
| **Registry** | `registries/manager.py` | Component discovery |
| **Observer** | `services/events/` | Event publishing |
| **State Machine** | `services/runs/` | Run lifecycle |
| **Strategy** | `core/tool_calling/` | Tool invocation abstraction |
| **Decorator** | Plugin registration | Auto-discovery |
| **Singleton** | `RegistryManager` | Global access point |

---

## Tech Stack

### Backend
- **Language**: Python 3.12
- **Framework**: FastAPI + Starlette
- **Database**: PostgreSQL (asyncpg), MySQL (asyncmy), Neo4j
- **ORM**: SQLAlchemy 2.0 + Alembic migrations
- **Cache**: Redis
- **Task Queue**: Celery
- **AI/LLM**: LangChain, LangGraph, DashScope, OpenAI, Ollama, FastMCP
- **Vector DB**: pgvector

### Frontend
- **Framework**: React (Vite)
- **Language**: TypeScript
- **Styling**: Tailwind CSS

## Development Setup

### Package Management
Use **uv** (astral uv) for Python dependency management:
```bash
# Install dependencies
uv sync

# Add a new dependency
uv add <package>

# Add dev dependency
uv add --dev <package>
```

### Environment Setup
```bash
# Create .env file from template
cd src && uv run sync-env
```

## Common Commands

### Service Management (via Makefile)
```bash
make start-api        # Start API server (port 8000)
make start-worker     # Start Worker service
make start-celery     # Start Celery Worker
make start-mcp        # Start MCP service (port 9000)
make start-frontend   # Start frontend dev server
make start-all        # Start all services
make stop-all         # Stop all services
```

### CLI Commands
```bash
uv run structure          # Unified service manager (recommended)
uv run structure-api      # API service
uv run structure-mcp      # MCP service
uv run structure-worker   # Worker service
```

### Database Migrations (Alembic)
```bash
make db-upgrade       # Upgrade to latest
make db-downgrade     # Rollback one version
make db-revision      # Create new migration (auto-detect)
make db-current       # Show current version
make db-history       # Show migration history
make db-status        # Show migration status
```

## Database Usage (SQLAlchemy 2.0 Async)

### Session 获取方式

```python
# API 路由中 - 使用依赖注入
from structure.core.dependencies import get_db


@router.get("/items")
async def get_items(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Item))
    return result.scalars().all()


# Worker/Service 中 - 使用 context manager
from structure.extensions.database import get_session

async with get_session("structure") as session:
    result = await session.execute(select(Run))
```

### 事务管理模式

#### ✅ 推荐：Auto-begin + auto_commit

```python
# 对于需要立即提交的操作，使用 auto_commit=True
await crud.create(data, auto_commit=True)
await state_machine.start(run_id, auto_commit=True)

# 对于批量操作，最后统一提交
await crud.create(item1, auto_commit=False)
await crud.create(item2, auto_commit=False)
await db.commit()
```

#### ❌ 避免：长事务中包含长时间操作

```python
# 错误示例 - Agent 执行不应在事务中
async with db.begin():
    run = await db.execute(select(Run))
    await agent.execute()  # ❌ 长时间操作会锁定事务
    await state_machine.complete(run_id)

# 正确示例 - 分离数据获取和执行
run = await db.execute(select(Run))  # Auto-begin
workspace_id = run.workspace_id

await agent.execute()  # 事务外执行
await state_machine.complete(run_id, auto_commit=True)  # 独立提交
```

#### ❌ 避免：重复使用的 Session 上调用 begin()

```python
# 错误 - Session 可能已有活跃事务
async with session.begin():  # InvalidRequestError!
    ...

# 正确 - 直接使用 session，依赖 auto-begin
result = await session.execute(select(Model))
await session.commit()
```

### 常见模式

| 场景 | 推荐做法 |
|------|----------|
| API 单次读取 | 直接 `execute()`，无需 commit |
| API 单次写入 | `auto_commit=True` 或手动 `commit()` |
| Worker 长时间任务 | 短事务获取数据，执行逻辑在事务外 |
| 批量操作 | 多次 `flush()`，最后一次 `commit()` |

### Docker
```bash
make docker-up        # Start containers
make docker-down      # Stop containers
```

## Project Structure

### Backend Structure (`src/aiwen/`)

```
src/aiwen/
├── core/                      # Foundation Layer (37,925 lines)
│   ├── bootstrap.py           # Unified initialization system
│   ├── interfaces/            # Abstract base classes
│   │   ├── executor.py        # Executor protocol with AgentEvent
│   │   ├── tool.py            # BaseTool with schema generation
│   │   └── protocols.py       # Protocol-based interfaces
│   ├── frameworks/            # Advanced frameworks
│   │   └── context_layer.py   # Path-addressable context store
│   ├── tool_calling/          # Tool invocation strategies
│   │   ├── strategy.py        # Abstract strategy interface
│   │   ├── function_calling.py # OpenAI function calling
│   │   └── prompt_calling.py  # Prompt-based tool calling
│   ├── enums/                 # System-wide enums
│   ├── constants/             # Constants and utilities
│   └── types/                 # Custom type definitions
│
├── services/                  # Business Logic Layer
│   ├── events/                # Event-sourcing services
│   │   ├── event_publisher.py # Dual-write (DB + Redis)
│   │   ├── event_worker.py    # Redis Stream consumer
│   │   ├── event_consumer.py  # Event stream processing
│   │   └── event_replayer.py  # Historical replay
│   ├── executor/              # Executor lifecycle
│   │   ├── runtime.py         # Task-based executor management
│   │   └── template_crud.py   # Template metadata
│   ├── runs/                  # Run management
│   │   ├── run_state_machine.py # State transitions
│   │   ├── run_crud.py        # Run CRUD operations
│   │   └── stuck_run_detector.py # Recovery system
│   ├── workspaces/            # Workspace services
│   │   ├── workspace_crud.py  # Workspace CRUD
│   │   └── workspace_context_service.py # Context management
│   ├── context/               # Context services
│   │   ├── knowledge/         # Knowledge base management
│   │   ├── tools/             # Tool services
│   │   └── skills/            # Skill management
│   ├── auth/                  # Authentication
│   └── storage/               # File storage (S3)
│
├── plugins/                   # Pluggable Components
│   ├── executors/             # Executor implementations
│   │   ├── simple/            # SimpleExecutor
│   │   └── conflict/          # ConflictExecutor (multi-turn reasoning)
│   └── tools/                 # Tool implementations (150+)
│       ├── context_tools/     # read, search, create, update, delete
│       ├── text_tools/        # parsing, summarization
│       ├── execution_tools/   # command execution
│       └── utility_tools/     # formatting, validation
│
├── registries/                # Registry & Discovery System
│   ├── core.py                # BaseRegistry with template method
│   ├── manager.py             # Singleton RegistryManager
│   ├── dynamic_loader.py      # Runtime tool loading
│   └── tool_service.py        # Tool metadata + invocation
│
├── routers/                   # API Layer (12 route groups)
│   ├── auth.py                # Authentication endpoints
│   ├── workspaces.py          # Workspace CRUD
│   ├── runs.py                # Run management
│   ├── events.py              # Event querying + SSE streaming
│   ├── context/               # Context management
│   │   ├── knowledge.py       # Knowledge base
│   │   ├── tools.py           # Tool management
│   │   ├── skills.py          # Skill templates
│   │   └── documents.py       # Document management
│   └── apps.py                # Agent application mgmt
│
├── models/                    # Data Layer (SQLAlchemy 2.0)
│   ├── event.py               # Event-sourced event log
│   ├── run.py                 # Execution unit
│   ├── workspace.py           # Workspace entity
│   ├── workspace_context.py   # Persistent context storage
│   ├── executor.py            # Executor template metadata
│   ├── app.py                 # Agent application
│   ├── user.py                # User account
│   └── context/               # Context models
│       ├── knowledge.py       # Knowledge base
│       ├── document.py        # Document metadata
│       └── tool.py            # Tool definitions
│
├── schemas/                   # Pydantic Schemas
│   ├── event.py               # Event request/response
│   ├── run.py                 # Run schemas
│   ├── workspace.py           # Workspace schemas
│   └── context/               # Context schemas
│
├── extensions/                # Low-level Infrastructure
│   ├── database.py            # Async DB engine factory
│   ├── logger.py              # Structured logging
│   ├── storage/               # S3 storage backend
│   └── llm/                   # Multi-provider LLM abstraction
│
├── config/                    # Configuration System
│   ├── factory.py             # LRU cached singleton
│   └── base.py                # Pydantic BaseSettings
│
├── migrations/                # Alembic Migrations
│   └── versions/              # Migration history
│
├── utils/                     # Utilities
│   ├── datetime.py            # Date/time helpers
│   ├── json.py                # JSON serialization
│   └── prompt.py              # Prompt utilities
│
├── app.py                     # FastAPI app initialization
├── worker_cli.py              # Worker process entry point
└── cli.py                     # CLI commands

frontend/src/                  # React + TypeScript Frontend
├── pages/                     # Page components
│   ├── HomePage.tsx           # Main dashboard
│   ├── ChatPage.tsx           # Chat interface
│   └── context/               # Context management pages
│       ├── KnowledgePage.tsx  # Knowledge base UI
│       ├── ToolPage.tsx       # Tool management
│       ├── SkillPage.tsx      # Skill management
│       ├── DocumentPage.tsx   # Document browser
│       └── MemoryPage.tsx     # Context memory viewer
├── services/                  # API services
│   ├── api.ts                 # Axios client
│   ├── authService.ts         # Authentication
│   ├── workspaceService.ts    # Workspace API
│   ├── runService.ts          # Run API
│   ├── eventService.ts        # Event API
│   └── chatService.ts         # Chat streaming
├── hooks/                     # React Query hooks
│   ├── useApps.ts             # App management
│   ├── useMemory.ts           # Memory/event hooks
│   └── useTools.ts            # Tool hooks
├── stores/                    # Zustand state management
│   ├── useChatStore.ts        # Chat state
│   ├── useAppStore.ts         # App state
│   └── useUIStore.ts          # UI preferences
├── types/                     # TypeScript types
├── constants/                 # API endpoints, constants
└── components/                # Reusable components

tests/                         # Test Suite
├── unit/                      # Unit tests
├── integration/               # Integration tests
└── fixtures/                  # Test fixtures

docs/                          # Documentation
└── prompt_usage.md            # Prompt system usage guide
```

### Key Statistics
- **Total Backend Code**: 37,925+ lines of Python
- **Core Modules**: 8 major service packages
- **API Endpoints**: 50+ REST endpoints across 12 routers
- **Database Models**: 20+ SQLAlchemy models
- **Tool Implementations**: 150+ built-in tools
- **Plugin System**: Dynamic executor + tool discovery
## Testing

```bash
# Run all tests
pytest

# Run with markers
pytest -m unit          # Unit tests only
pytest -m integration   # Integration tests
pytest -m smoke         # Smoke tests

# Coverage report
pytest --cov=src/structure --cov-report=html
```

- **Framework**: pytest + pytest-asyncio
- **Coverage threshold**: 60%
- **Test paths**: `tests/`

## Commit Conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/):
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Code style (no logic change)
- `refactor`: Code refactoring
- `perf`: Performance improvement
- `test`: Add/modify tests
- `build`: Build system changes
- `ci`: CI configuration
- `chore`: Miscellaneous

Example: `feat(auth): add user login endpoint`

## Key Configuration Files

- `pyproject.toml` - Python project config, dependencies
- `ruff.toml` - Linting/formatting rules
- `alembic.ini` - Database migration config
- `.env.example` - Environment variables template

---

## System Characteristics

### Scalability Features
- ✅ **Horizontal scaling**: Multiple worker instances with consumer groups
- ✅ **Event-sourced**: No distributed locking needed for state management
- ✅ **Async I/O**: High concurrency with connection pooling (20-30 per pool)
- ✅ **Redis Streams**: Proper consumer group handling for task distribution
- ✅ **Stateless API**: API servers can be scaled independently

### Reliability Features
- ✅ **Stuck run detection**: Automatic recovery for unresponsive runs
- ✅ **State machine validation**: Prevents invalid state transitions
- ✅ **Multi-level health checks**: DB, Redis, cache status monitoring
- ✅ **Graceful shutdown**: Signal handling (SIGINT, SIGTERM)
- ✅ **Event persistence**: All state saved in PostgreSQL before broadcasting
- ✅ **Immutable event log**: Full audit trail of all operations

### Observability
- ✅ **Structured logging**: Per-component log levels via structlog
- ✅ **Event audit trail**: Complete immutable history in event table
- ✅ **Health endpoints**: `/health` with component-level status
- ✅ **Request tracing**: Logging middleware with request timing
- ✅ **Event sequencing**: Ordered event streams per run/workspace

---

## Key Implementation Files

| File | Lines | Purpose |
|------|-------|---------|
| `src/aiwen/app.py` | 93 | FastAPI app initialization |
| `src/aiwen/core/bootstrap.py` | 477 | Unified bootstrap system |
| `src/aiwen/worker_cli.py` | 215 | Worker process entry point |
| `src/aiwen/services/events/event_worker.py` | 200+ | Event consumption + executor dispatch |
| `src/aiwen/services/events/event_publisher.py` | 150+ | Dual-write (DB + Redis) event publishing |
| `src/aiwen/services/executor/runtime.py` | 78 | Executor lifecycle management |
| `src/aiwen/services/runs/run_state_machine.py` | 200+ | Run state transitions |
| `src/aiwen/registries/core.py` | 250+ | Registry abstraction layer |
| `src/aiwen/core/interfaces/executor.py` | 200+ | Executor protocol definition |
| `src/aiwen/core/tool_calling/strategy.py` | 83+ | Tool calling strategy interface |
| `src/aiwen/extensions/database.py` | 300+ | Async DB infrastructure |

---

## Architecture Principles

### Event-Sourcing Principles
1. **All state changes are events**: User messages, agent responses, tool executions, state transitions
2. **Immutable event log**: PostgreSQL event table is append-only
3. **Event sequencing**: Auto-sequenced per run and workspace for ordering guarantees
4. **Real-time broadcasting**: Redis Streams for immediate SSE client updates
5. **Event replay**: Historical reconstruction via EventReplayer

### Async Architecture
- **AsyncIO**: All I/O operations are non-blocking
- **SQLAlchemy async**: AsyncSession with asyncpg/asyncmy drivers
- **Redis async**: redis.asyncio.Redis client for stream operations
- **Worker concurrency**: asyncio.gather() for parallel task processing
- **Transaction management**: Short transactions, long operations outside DB locks

### Plugin Architecture
- **Decorator-based registration**: `@register_tool` and `@register_executor`
- **Auto-discovery**: Import triggers registration in global registry
- **Database sync**: Metadata automatically synced to PostgreSQL
- **Runtime instantiation**: Tools and executors loaded on-demand
- **Metadata-driven**: JSON schemas, descriptions stored in registry

---

## Additional Resources

### Documentation
- `docs/prompt_usage.md` - Prompt system usage and best practices
- `CLAUDE.md` - This file (architecture and development guide)
- `README.md` - Project overview and quick start

### Configuration Examples
- `src/.env.example` - Environment variables template
- `alembic.ini` - Database migration configuration
- `pyproject.toml` - Dependencies and project metadata

### Frontend Documentation
- See `frontend/README.md` for React app setup
- TypeScript types in `frontend/src/types/`
- API integration patterns in `frontend/src/services/`

Add under a new ## Docker & Deployment section\n\nWhen fixing Docker/deployment issues, always verify the full build-and-run cycle before reporting success. Check: Dockerfile paths, volume mounts, network connectivity between containers, and that all required files are included in the build context.
Add under ## General Rules at the top of CLAUDE.md\n\nAfter implementing a feature, run the application and verify it works end-to-end before marking complete. Pay special attention to: database migrations (check ordering and idempotency), import paths, and runtime errors that don't appear at build time.
Add under ## Project Overview section at the top of CLAUDE.md\n\nThis project uses Python (backend) and TypeScript (frontend). Backend uses SQLite with alembic migrations, S3/MinIO for storage, WebSockets for real-time communication, and Docker Compose for deployment. When making changes, consider impacts across both backend and frontend.
Add under ## Debugging section\n\nWhen debugging errors, diagnose the actual root cause before applying fixes. Do not guess-and-check iteratively. Read the relevant source code, trace the error path, and confirm the cause before editing.
Add under ## Database section\n\nFor database migrations: ensure migration scripts are idempotent (handle already-existing tables/columns), verify migration ordering relative to startup code that depends on new schema, and test with a fresh database.