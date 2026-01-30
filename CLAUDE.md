# CLAUDE.md

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

## Code Style

### Linting & Formatting
- **Tool**: Ruff (v0.14.7+)
- **Line length**: 88
- **Python version**: 3.12

```bash
# Format code
ruff format .

# Check linting
ruff check .

# Fix auto-fixable issues
ruff check --fix .
```

### Key Style Rules
- Double quotes for strings
- Space indentation
- isort for import sorting (first-party package: `app`)

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
