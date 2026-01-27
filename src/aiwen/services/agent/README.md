# Agent System Documentation

## Overview

The agent system provides a flexible framework for executing different types of AI-powered tasks. The system follows a clean architecture where:

1. **Database** stores agent metadata (agent_id, agent_type, config)
2. **Agent Factory** routes to the appropriate concrete agent class
3. **Concrete Agents** implement specific workflows in code

## Architecture

```
Request
  |
  v
FastAPI
  |
  v
Load Agent Meta (PG)
  |
  v
Agent Router (agent_key / agent_type)
  |
  v
Concrete Agent Class (code-defined workflow)
```

## Components

### 1. Agent Model (`models/agents/agent.py`)

Defines the `Agent` SQLAlchemy model with fields:
- `id`: UUID primary key
- `agent_id`: Unique string identifier (used in API)
- `agent_type`: Maps to concrete agent class
- `enabled`: Whether the agent is active
- `config`: JSONB configuration for the agent
- `version`: Version number
- `created_at`: Creation timestamp
- `updated_at`: Last update timestamp

### 2. Base Agent (`services/agents/base.py`)

Abstract base class that all agents must inherit from:

```python
class BaseAgent(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        pass
```

### 3. Concrete Agents (`services/agents/concrete.py`)

Implementation of specific agent types:
- `NL2SQLAgent`: Converts natural language to SQL
- `AnomalyAgent`: Detects anomalies in data

### 4. Agent Factory (`services/agents/factory.py`)

Routes agent_type strings to concrete agent classes:

```python
agent = AgentFactory.create("NL2SQLAgent", config)
```

### 5. Agent CRUD Operations (`services/agents/crud.py`)

Provides database operations for managing agents:
- Create, Read, Update, Delete operations
- List agents with pagination
- Enable/disable agents

### 6. Agent Manager (`services/agents/manager.py`)

High-level manager that combines CRUD operations with factory creation:
- Simplified interface for agent management
- Run agents with automatic lookup and instantiation

### 7. API Router (`routers/agents/router.py`)

Provides REST endpoints:
- `POST /api/agents/`: Create a new agent
- `GET /api/agents/`: List all agents
- `GET /api/agents/{agent_id}`: Get agent details
- `PUT /api/agents/{agent_id}`: Update an agent
- `DELETE /api/agents/{agent_id}`: Delete an agent
- `POST /api/agents/{agent_id}/run`: Execute an agent
- `GET /api/agents/types`: List available agent types

## Database Schema

```sql
CREATE TABLE agent (
    id           UUID PRIMARY KEY,
    agent_id     TEXT UNIQUE NOT NULL,
    agent_type   TEXT NOT NULL,
    enabled      BOOLEAN DEFAULT TRUE,
    config       JSONB,
    version      INTEGER DEFAULT 1,
    created_at   TIMESTAMP DEFAULT now(),
    updated_at   TIMESTAMP
);
```

## Example Usage

1. Create an agent through the API:
   ```bash
   curl -X POST http://localhost:8000/api/agents/ \
        -H "Content-Type: application/json" \
        -d '{
          "agent_id": "my-nl2sql-agent",
          "agent_type": "NL2SQLAgent",
          "enabled": true,
          "config": {"model": "gpt-4.1"},
          "version": 1
        }'
   ```

2. Run the agent:
   ```bash
   curl -X POST http://localhost:8000/api/agents/my-nl2sql-agent/run \
        -H "Content-Type: application/json" \
        -d '{"query": "Show me sales data for last month"}'
   ```

3. List all agents:
   ```bash
   curl -X GET http://localhost:8000/api/agents/
   ```

## Benefits

1. **Strong Typing & Readability**: Agent workflows are defined in code
2. **Database Changes ≠ Logic Changes**: Modifying agent metadata doesn't affect execution
3. **Complex Agent Support**: Handles conditions, branches, loops, parallelism, error handling
4. **Platform Control**: Enable/disable agents, versioning, tenant isolation, rollout control
5. **Full CRUD Operations**: Complete lifecycle management through REST API
6. **PostgreSQL Optimization**: Uses PostgreSQL-specific features like JSONB for flexible configuration storage