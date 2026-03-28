# Aiwen Project Overview

Aiwen is an enterprise-grade, event-sourced, async-first AI agent platform designed for running intelligent AI agents at scale. It provides a complete orchestration layer encompassing LLM interaction, tool execution, real-time streaming, and distributed worker coordination.

## Architecture & Technologies

The project is structured as a monorepo containing both the backend service and the frontend web application.

- **Backend (`src/aiwen/`)**: 
  - **Language**: Python 3.14+
  - **Framework**: FastAPI
  - **Database & ORM**: PostgreSQL with SQLAlchemy 2.0 (async), Alembic for migrations
  - **Caching & Messaging**: Redis (Streams for task queues/event broadcast, Cache for session data)
  - **Architecture**: Event-sourced state machine, Plugin-based tool and executor registries, Distributed workers (via Redis Streams and Celery)
  - **Dependency Management**: `uv`

- **Frontend (`frontend/`)**:
  - **Language**: TypeScript
  - **Framework**: React 19 + Vite
  - **Styling & UI**: Tailwind CSS, Radix UI primitives, Lucide React icons
  - **State Management**: Zustand, React Query
  - **Dependency Management**: `npm`

## Building and Running

### Prerequisites
- Python 3.14+
- Node.js & npm
- PostgreSQL 14+
- Redis 7+
- `uv` (Python package manager)

### Local Development Setup

1. **Install Dependencies**:
   ```bash
   # Backend
   uv sync

   # Frontend
   cd frontend && npm install
   ```

2. **Environment Setup**:
   ```bash
   cd src && uv run sync-env  # Generates .env from .env.example
   ```

3. **Database Migrations**:
   ```bash
   make db-upgrade
   ```

### Running Services

You can start all local services (API, Worker, Celery, Frontend) concurrently using the Makefile:
```bash
make start-all
```

Alternatively, you can run them individually:
- **API Server**: `make start-api` (Port 8000)
- **Worker**: `make start-worker`
- **Celery Worker**: `make start-celery`
- **Frontend**: `make start-frontend`
- **MCP Service**: `make start-mcp` (Port 9000)

To stop all services:
```bash
make stop-all
```

### Docker
To run the infrastructure using Docker Compose:
```bash
make docker-up
# Or manually: docker compose -f docker/docker-compose.yml --env-file .env up -d
```

## Development Conventions

- **Code Formatting & Linting**: 
  - Python: Uses `ruff` for fast linting and formatting.
  - TypeScript/Frontend: Uses `eslint`.
- **Testing**: 
  - Backend uses `pytest` (`make test`).
  - Target coverage is 60%.
- **Commit Messages**: 
  - The project follows **Conventional Commits** (e.g., `feat:`, `fix:`, `docs:`, `refactor:`).
- **Database Changes**: 
  - Manage migrations using `make db-revision` to autogenerate and `make db-upgrade` to apply. Keep database transactions short and never hold them open during LLM calls.
