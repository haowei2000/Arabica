# CLAUDE.md
Dont run format check and other unnecessary oprerations.
## Project Overview

Aiwen Service (aiwen-service v5.5.0) - A Project Management and Collaboration Tool with AI-powered agent capabilities.

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
uv run aiwen          # Unified service manager (recommended)
uv run aiwen-api      # API service
uv run aiwen-mcp      # MCP service
uv run aiwen-worker   # Worker service
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
from aiwen.dependencies.database import get_db

@router.get("/items")
async def get_items(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Item))
    return result.scalars().all()

# Worker/Service 中 - 使用 context manager
from aiwen.extensions.database import get_session

async with get_session("aiwen") as session:
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

```
src/aiwen/
├── routers/          # API route handlers
├── services/         # Business logic
├── models/           # SQLAlchemy models
├── schemas/          # Pydantic schemas
├── migrations/       # Alembic migrations
├── config/           # Configuration
├── middleware/       # FastAPI middleware
├── workers/          # Background workers
├── utils/            # Utilities
└── core/             # Core functionality

frontend/src/         # React frontend
tests/                # Test suite
docs/                 # Documentation
```
## Testing

```bash
# Run all tests
pytest

# Run with markers
pytest -m unit          # Unit tests only
pytest -m integration   # Integration tests
pytest -m smoke         # Smoke tests

# Coverage report
pytest --cov=src/aiwen --cov-report=html
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
