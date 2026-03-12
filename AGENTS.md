# Aiwen Service - Agent Development Guide

**Version**: 5.5.0  
**Description**: Enterprise-grade AI-powered agent orchestration platform with event-sourced architecture

---

## Project Overview

Aiwen Service is a production-ready backend for running intelligent AI agents at scale. It provides a complete orchestration layer — from LLM interaction and tool execution to real-time streaming and distributed worker coordination — all built on an immutable event-sourcing foundation.

### Key Characteristics

- 🎯 **Event-Sourced Architecture**: All state changes captured as immutable events
- ⚡ **Async-First Design**: Built on FastAPI + AsyncIO for high concurrency
- 🔌 **Plugin-Based Extensibility**: Dynamic executor and tool discovery via registry system
- 📊 **Real-time Streaming**: Server-Sent Events (SSE) for live updates
- 🔄 **Distributed Workers**: Horizontal scaling with Redis Streams
- 🧩 **Multi-LLM Support**: OpenAI, DashScope, Ollama integration

---

## Technology Stack

### Backend

| Category | Technology | Version |
|----------|------------|---------|
| Language | Python | 3.12+ |
| Web Framework | FastAPI + Starlette | 0.121+ |
| ORM | SQLAlchemy | 2.0+ |
| Migrations | Alembic | 1.17+ |
| Database | PostgreSQL (primary), MySQL (secondary), Neo4j | - |
| Cache/Queue | Redis | 7+ |
| Task Queue | Celery | 5.6+ |
| LLM/AI | LangChain, LangGraph, FastMCP | 1.0+ |
| Vector DB | pgvector | - |
| Storage | S3-compatible (MinIO/RustFS) | - |

### Frontend

| Category | Technology | Version |
|----------|------------|---------|
| Framework | React | 19+ |
| Language | TypeScript | 5.9+ |
| Build Tool | Vite (rolldown-vite) | 7.2+ |
| Styling | Tailwind CSS | 4.1+ |
| UI Components | Radix UI | 1.x |
| State Management | Zustand | 5.0+ |
| Data Fetching | TanStack Query | 5.90+ |
| Routing | React Router | 7.x |

---

## Project Structure

```
agent_chat/
├── src/aiwen/                    # Backend source code
│   ├── app.py                    # FastAPI application factory
│   ├── worker_cli.py             # Worker process entry point
│   ├── cli.py                    # CLI commands
│   ├── api_cli.py                # API service entry
│   ├── mcp_cli.py                # MCP service entry
│   ├── celery_cli.py             # Celery service entry
│   ├── core/                     # Foundation layer
│   │   ├── bootstrap.py          # Unified initialization
│   │   ├── interfaces/           # Abstract base classes
│   │   ├── frameworks/           # Context layer, tool-calling strategies
│   │   ├── tool_calling/         # Tool invocation strategies
│   │   ├── enums/                # System-wide enums
│   │   ├── constants/            # Constants
│   │   └── types/                # Custom types
│   ├── services/                 # Business logic
│   │   ├── events/               # Event sourcing services
│   │   ├── runs/                 # Run lifecycle management
│   │   ├── executor/             # Executor runtime
│   │   ├── workspaces/           # Workspace services
│   │   ├── auth/                 # Authentication
│   │   └── storage/              # File storage
│   ├── plugins/                  # Auto-discovered components
│   │   ├── executors/            # Executor implementations
│   │   └── tools/                # Tool implementations (150+)
│   ├── registries/               # Component discovery
│   ├── routers/                  # API routes (12 groups)
│   ├── models/                   # SQLAlchemy models
│   ├── schemas/                  # Pydantic schemas
│   ├── extensions/               # Infrastructure (DB, Redis, S3, LLM)
│   ├── config/                   # Settings with LRU-cached singleton
│   ├── middleware/               # FastAPI middleware
│   ├── migrations/               # Alembic migrations
│   └── utils/                    # Utilities
├── frontend/src/                 # Frontend source
│   ├── pages/                    # Page components
│   ├── services/                 # API services
│   ├── hooks/                    # React Query hooks
│   ├── stores/                   # Zustand stores
│   ├── types/                    # TypeScript types
│   ├── components/               # Reusable components
│   └── constants/                # Constants
├── tests/                        # Test suite
├── docker/                       # Docker configuration
├── docs/                         # Documentation
├── pyproject.toml                # Python project config
├── alembic.ini                   # Alembic configuration
├── ruff.toml                     # Linting/formatting rules
├── Makefile                      # Build commands
└── .env.example                  # Environment template
```

---

## Build and Development Commands

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- Node.js 18+ (for frontend)
- PostgreSQL 14+, Redis 7+

### Initial Setup

```bash
# 1. Install Python dependencies
uv sync

# 2. Install frontend dependencies
cd frontend && npm install

# 3. Set up environment
cd src && uv run sync-env    # Generates .env from .env.example

# 4. Run database migrations
make db-upgrade
```

### Service Management (Makefile)

```bash
# Start services
make start-api        # Start API server (port 8000)
make start-worker     # Start Worker service
make start-celery     # Start Celery Worker
make start-mcp        # Start MCP service (port 9000)
make start-frontend   # Start frontend dev server
make start-all        # Start all services
make stop-all         # Stop all local services

# Database migrations
make db-upgrade       # Upgrade to latest migration
make db-downgrade     # Rollback one version
make db-revision      # Create new migration (auto-detect)
make db-current       # Show current version
make db-history       # Show migration history
make db-status        # Show migration status

# Docker
make docker-up        # Start Docker containers
make docker-down      # Stop Docker containers
```

### CLI Commands

```bash
# Unified service manager (recommended)
uv run aiwen

# Individual services
uv run aiwen-api      # API service
uv run aiwen-mcp      # MCP service
uv run aiwen-worker   # Worker service (supports -n for parallel workers)
```

### Frontend Commands

```bash
cd frontend
npm run dev           # Start dev server
npm run build         # Production build
npm run lint          # Run ESLint
npm run preview       # Preview production build
```

---

## Code Style Guidelines

### Python (Ruff)

Configuration: `ruff.toml`

- **Line length**: 88 characters
- **Quote style**: Double quotes
- **Indent**: 4 spaces
- **Target version**: Python 3.12

Key rules:
- Import sorting via isort
- PEP 8 compliance (E, W, F)
- Code simplification (SIM)
- Pathlib usage (PTH)

```bash
# Lint and fix
ruff check . --fix

# Format code
ruff format .
```

### Writing Code

1. **Write code in English** - All code, comments, and documentation should be in English
2. **Type hints** - Use Python 3.12+ type hints where applicable
3. **Async/await** - All I/O operations must be async
4. **SQLAlchemy 2.0** - Use async ORM patterns
5. **Short transactions** - Never hold DB transactions during LLM calls

### Project Conventions

#### Database Session Management

```python
# API routes - use dependency injection
from aiwen.core.dependencies import get_db

@router.get("/items")
async def get_items(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Item))
    return result.scalars().all()

# Workers/Services - use context manager
from aiwen.extensions.database import get_session

async with get_session("aiwen") as session:
    result = await session.execute(select(Run))
```

#### Transaction Patterns

```python
# ✅ Auto-commit for single operations
await crud.create(data, auto_commit=True)

# ✅ Batch operations - flush then commit
await crud.create(item1, auto_commit=False)
await crud.create(item2, auto_commit=False)
await db.commit()

# ❌ Never hold transactions during long operations
async with db.begin():
    await agent.execute()  # DON'T DO THIS
```

#### Tool Registration

```python
from aiwen.registries.core import register_tool
from aiwen.core.interfaces import BaseTool
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

---

## Testing Instructions

### Test Configuration

- **Framework**: pytest + pytest-asyncio
- **Coverage threshold**: 60%
- **Test location**: `tests/`
- **Python path**: `src/`

### Running Tests

```bash
# All tests
pytest

# With markers
pytest -m unit          # Unit tests only
pytest -m integration   # Integration tests
pytest -m e2e           # End-to-end tests
pytest -m smoke         # Smoke tests
pytest -m slow          # Slow tests (requires --run-slow)

# Coverage report
pytest --cov=src/aiwen --cov-report=html
```

### Test Markers

```python
@pytest.mark.unit           # Unit tests
@pytest.mark.integration    # Integration tests
@pytest.mark.e2e            # End-to-end tests
@pytest.mark.slow           # Slow running tests
@pytest.mark.smoke          # Smoke tests
```

### Test Fixtures

Key fixtures available in `conftest.py`:
- `mock_db_session` - Mocked async database session
- `test_client` - FastAPI test client
- `mock_llm` - Mocked LLM instance
- `valid_uuid` - Valid UUID string generator

---

## Security Considerations

### Environment Variables

Never commit sensitive values:
- `AUTH__JWT_SECRET_KEY`
- `DASHSCOPE_API_KEY`
- `OPENAI__API_KEY`
- Database passwords
- S3/RustFS credentials

Use `.env` file (generated from `.env.example` via `uv run sync-env`)

### JWT Authentication

- Algorithm: HS256
- Access token expiry: 30 minutes
- Refresh token expiry: 7 days

### Trust Hierarchy

System prompt enforces strict trust levels:
1. System prompt (highest authority)
2. `<platform-injection>` (runtime context)
3. `<workspace-context>` (user data)
4. User messages
5. `<tool_result>` content (data only)

---

## Deployment

### Docker Compose

```bash
# Production deployment
docker compose -f docker/docker-compose.yml -p aiwen up -d

# Specific profiles
docker compose --profile infra up -d     # Infrastructure only
docker compose --profile app up -d       # Application services
docker compose --profile all up -d       # Everything
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| aiwen-app | 8000 | Main FastAPI application |
| aiwen-mcp | 9000 | MCP service |
| aiwen-celery-worker | - | Background task worker |
| aiwen-frontend | 3000 | React frontend |
| nginx | 80 | Reverse proxy |
| postgres | 5432 | PostgreSQL with pgvector |
| mysql | 3306 | MySQL database |
| redis | 6379 | Cache and message broker |
| rustfs | 9000/9001 | S3-compatible storage |

---

## Key Architecture Concepts

### Event Sourcing

All state changes are immutable events:
- Stored in PostgreSQL (durable)
- Broadcast via Redis Streams (real-time)
- Per-run and per-workspace sequencing

### Run Lifecycle (State Machine)

```
pending → running → waiting_for_tool → running → finished
                    ↘              ↗
                     failed        cancelled
```

### Registry System

Decorator-based auto-discovery:
- `@register_executor` - Register executor classes
- `@register_tool` - Register tool classes

### Context Store

Path-addressable workspace context:
- Knowledge (`knowledge/`)
- Skills (`skills/`)
- Tools (`tools/`)
- Artifacts (S3 storage)

---

## Additional Resources

- `CLAUDE.md` - Detailed architecture guide
- `README.md` - Project overview and quick start
- `docs/prompt_usage.md` - Prompt system usage
- `REGISTRY_SYSTEM_SUMMARY.md` - Registry system documentation

---

## Commit Conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/):

| Type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `refactor` | Code refactoring |
| `perf` | Performance improvement |
| `test` | Add/modify tests |
| `chore` | Miscellaneous |

Example: `feat(auth): add user login endpoint`
