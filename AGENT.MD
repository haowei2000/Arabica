# Structure System Functionality Summary

Last updated: 2026-05-09

This document summarizes the current functional surface of the Structure
system. It is intended as a product and architecture inventory, not as a
developer instruction file. For agent coding rules, use `AGENTS.md`.

## 1. System Purpose

Structure is an event-sourced AI agent orchestration platform. It provides a
complete stack for creating workspaces, running AI agents, streaming run
events to the browser, managing tools and context, and operating distributed
workers backed by PostgreSQL and Redis.

The system is designed around these core capabilities:

- AI agent execution with streaming chat responses.
- Immutable event sourcing for all run and workspace activity.
- Workspace-scoped context, knowledge, skills, tools, tasks, and artifacts.
- Dynamic executor and tool registration.
- Built-in tools exposed through an internal MCP server.
- External MCP tool probing and importing.
- Multi-model configuration for chat, vision-capable chat, and embeddings.
- Docker-first local deployment with backend, frontend, PostgreSQL, Redis,
  and S3-compatible object storage.

## 2. Major User-Facing Areas

### 2.1 Authentication and User Session

The frontend exposes a login flow at `/login`.

Current capabilities:

- Username/password login.
- JWT access and refresh token handling.
- Current-user lookup.
- Logout and local session cleanup.
- Automatic redirect to login when unauthenticated API requests return 401.
- Admin bootstrap support through environment configuration.

Backend API areas:

- `/api/auth/login`
- `/api/auth/refresh`
- `/api/auth/me`
- User creation, listing, update, and deletion under `/api/users`.
- Friend request and friend relationship management under `/api/friends`.

### 2.2 Workspace Console

The main working surface is `/app`. It combines workspace management, chat,
run history, context inspection, tasks, artifacts, and workspace settings.

Current capabilities:

- List all user workspaces.
- Auto-create a default workspace when none exists.
- Create workspaces.
- Edit workspace metadata.
- Delete workspaces.
- Select the active workspace.
- Show workspace status and run count.
- Collapse and expand the workspace sidebar.
- Open a full agent console for the selected workspace.

Workspace settings currently support:

- Selecting an executor template.
- Setting workspace model provider and model name fields.
- Updating workspace execution-related configuration.
- Reinitializing workspace context through the backend API.

Backend API areas:

- `/api/workspaces`
- `/api/workspaces/{workspace_id}`
- `/api/workspaces/{workspace_id}/members`
- `/api/workspaces/{workspace_id}/contexts`
- `/api/workspaces/{workspace_id}/contexts/copy`
- `/api/workspaces/{workspace_id}/context/reinit`

### 2.3 Chat and Run Execution

The chat console lets a user send messages into an agent run and receive
streaming output.

Current capabilities:

- Send user messages from the browser.
- Create new runs.
- Load previous run history.
- Stream live agent output.
- Show user messages and assistant messages.
- Display streamed token output and token counters.
- Stop generation while a stream is active.
- Retry retryable stream errors.
- Resume runs that pause for user feedback.
- Submit human feedback to a waiting run.
- Use `@tool_name` mention autocomplete for forced tool invocation.
- Show run timeline items in the right panel.
- Show run status, token counts, and relative timestamps.

Backend run lifecycle:

- `pending`
- `running`
- `waiting_for_tool`
- `waiting_for_user`
- `finished`
- `failed`
- `cancelled`

Backend API areas:

- `/api/workspaces/{workspace_id}/runs`
- `/api/workspaces/{workspace_id}/runs/{run_id}`
- `/api/workspaces/{workspace_id}/runs/{run_id}/start`
- `/api/workspaces/{workspace_id}/runs/{run_id}/cancel`
- `/api/workspaces/{workspace_id}/runs/{run_id}/resume`
- `/api/workspaces/{workspace_id}/runs/{run_id}/feedback`

### 2.4 Run Side Panel

The workspace console right panel contains five operational tabs.

Runs:

- List recent runs for the workspace.
- Start a new run.
- Select and load an existing run.
- Display live status updates from SSE events.

Context:

- Show the workspace context tree.
- Expand path-addressable context nodes.
- Refresh the tree.
- Inspect context entries attached to the workspace.

Tasks:

- List tasks created by agents or tools.
- Show task status, assignee, run association, description, and result.
- Refresh task data.

Results:

- List generated artifacts.
- Show artifact metadata.
- Download artifacts through authenticated API requests.
- Refresh artifact data.

Settings:

- Configure workspace executor and model settings.
- Save workspace-level changes.

### 2.5 Apps and Executors

The system has an application/executor layer for reusable agent app
configuration.

Current capabilities:

- List available executor templates.
- Create app records.
- Get app details.
- List apps.
- Update apps.
- Delete apps.
- Query apps.
- Launch chat from an app-backed workspace flow.

Built-in executor implementations:

- `DefaultAgent`
- `ComplexAgent`
- `SimpleAgent`

Backend API areas:

- `/api/apps/executors/list`
- `/api/apps/create`
- `/api/apps/{app_id}/get`
- `/api/apps/list`
- `/api/apps/{app_id}/update`
- `/api/apps/{app_id}/delete`
- `/api/apps/query`

### 2.6 Trigger Management

The Trigger page is available through the home page and `/triggers`.
Triggers connect events to tool actions.

Current capabilities:

- Create triggers.
- Edit triggers.
- Delete triggers.
- Enable or disable triggers.
- View triggers in card, list, and accordion-style layouts.
- Configure trigger event type.
- Configure trigger priority.
- Configure condition type:
  - `always`
  - `keyword`
  - `regex`
  - `jsonpath`
- Configure condition field and value.
- Pick a registered inner tool.
- Generate a dynamic parameter form from the selected tool JSON schema.
- Save action parameters.
- Auto-inject `workspace_id` for trigger tool execution.

Backend API areas:

- Workspace-scoped triggers:
  - `/api/workspaces/{workspace_id}/triggers`
  - `/api/workspaces/{workspace_id}/triggers/{trigger_id}`
  - `/api/workspaces/{workspace_id}/triggers/{trigger_id}/test`
- User/global triggers:
  - `/api/triggers`
  - `/api/triggers/{trigger_id}`

### 2.7 Knowledge Management

Knowledge is managed from the home page Context tab under Knowledge.

Current capabilities:

- Create knowledge bases.
- List knowledge bases.
- Delete knowledge bases.
- Show knowledge status, permission, indexing technique, document count,
  and chunk count.
- Open the document management page for a knowledge base.
- Open search mode for a knowledge base.
- View generated context for a knowledge base.
- Support public/private permission labels.
- Support active, inactive, indexing, and error statuses.

Backend API areas:

- `/api/agent/knowledge`
- `/api/agent/knowledge/{knowledge_id}/get`
- `/api/agent/knowledge/{knowledge_id}/update`
- `/api/agent/knowledge/{knowledge_id}/delete`
- `/api/agent/knowledge/{knowledge_id}/context`
- `/api/agent/knowledge/query`
- `/api/agent/knowledge/search`
- `/api/agent/knowledge/{knowledge_id}/hybrid-search`

### 2.8 Document Management

Documents belong to knowledge bases and are converted into searchable context.

Current capabilities:

- Upload documents into a knowledge base.
- Start document processing/indexing.
- Track asynchronous task status.
- List documents by knowledge base.
- View document details.
- Delete documents.
- Download documents.
- Preview parsed document content.
- View context chunks produced from a document.
- Trigger context sync tasks through Celery.

Backend API areas:

- `/api/agent/document`
- `/api/agent/document/task/{task_id}/status`
- `/api/agent/document/knowledge/{knowledge_id}`
- `/api/agent/document/{document_id}`
- `/api/agent/document/{document_id}/context`
- `/api/agent/document/{document_id}/delete`
- `/api/agent/document/{document_id}/download`
- `/api/agent/document/{document_id}/preview`

### 2.9 Context Store

The context store is path-addressable and workspace-aware. It is used by
agents, knowledge bases, skills, tools, memories, and workspace context
selection.

Current capabilities:

- Create context records.
- Update context records.
- Delete context records.
- Batch fetch by IDs.
- Query context records.
- Grep context content.
- List memory context.
- Get context by ID.
- Vector search.
- Hybrid search.
- Batch delete context records by source.
- List chunks for a knowledge base.
- List chunks for a document.
- Copy selected context records into a workspace.
- Reinitialize workspace context.
- Display workspace context as a tree in the frontend.

Backend API areas:

- `/api/agent/context`
- `/api/agent/context/batch/ids`
- `/api/agent/context/{context_id}`
- `/api/agent/context/{context_id}/update`
- `/api/agent/context/{context_id}/delete`
- `/api/agent/context/query`
- `/api/agent/context/grep`
- `/api/agent/context/memories`
- `/api/agent/context/vector-search`
- `/api/agent/context/hybrid-search`
- `/api/agent/context/batch/delete-by-source/{source_id}`
- `/api/agent/context/knowledge/{knowledge_id}/chunks`
- `/api/agent/context/document/{document_id}/chunks`

### 2.10 Tool Management

Tools are managed from the home page Context tab under Tool.

Current capabilities:

- List all tools.
- Filter tools by tags.
- Group tools by bundle.
- Collapse and expand tool bundles.
- View tools as cards, list rows, or accordion detail rows.
- Show tool name, display name, category, type, status, usage count, tags,
  schema, and created time.
- Enable or disable non-inner tools.
- Delete non-inner tools.
- Test a tool with JSON parameters.
- View tool-generated context.
- Probe external MCP servers.
- Import selected MCP tools.
- Mark imported MCP tools as public.
- Group imported MCP tools into bundles.

Tool types represented by the UI:

- Built-in inner tools.
- MCP tools.
- HTTP tools.
- Server-run tools.

Backend API areas:

- `/api/agent/tools`
- `/api/agent/tools/inner`
- `/api/agent/tools/{tool_id}`
- `/api/agent/tools/{tool_id}/toggle`
- `/api/agent/tools/{tool_id}/test`
- `/api/agent/tools/probe-mcp`
- `/api/agent/tools/import-from-mcp`
- `/api/agent/tool-bundles`
- `/api/agent/tool-bundles/{bundle_id}`

### 2.11 Built-In Tool Inventory

The codebase currently includes these built-in tool categories.

Artifact tools:

- `create_artifact`
- `read_artifact`
- `update_artifact`
- `delete_artifact`
- `list_artifacts`

Context tools:

- `create_context`
- `read_context`
- `update_context`
- `delete_context`
- `list_context`
- `search_context`
- `glob_context`
- `tree_context`
- `glance_context`
- `rate_context`

Execution tools:

- `ask_for_user`
- `client_request`
- `code_execution`
- `confirm_action`
- `http_request`
- `sandbox_execution`

Task tools:

- `create_task`
- `update_task`
- `delete_task`
- `list_tasks`

Text tools:

- `grep`
- `structurize_text`

Utility tools:

- `cache_get`
- `cache_set`
- `get_run_history`
- `get_workspace_history`
- `get_workspace_info`

### 2.12 Skill Management

Skills are managed from the home page Context tab under Skill.

Current capabilities:

- List skills.
- Filter skills by tags.
- View skills as cards, list rows, or accordion detail rows.
- Edit skill name, description, content, and tags.
- Delete skills.
- Upload a skill folder.
- Parse `SKILL.md` frontmatter for name, description, and tags.
- Select files from an uploaded folder tree.
- Identify the main `SKILL.md` file.
- Show included files, file sizes, and content types.
- View generated context for a skill.
- Open a skill file browser route.

Backend API areas:

- `/api/agent/skills`
- `/api/agent/skills/{skill_id}`
- `/api/agent/skills/{skill_id}/files`
- Skill upload and sync endpoints under `/api/agent/skills`.

### 2.13 Memory and Event Browsing

Memory is managed from the home page Context tab under Memory.

Current capabilities:

- Browse workspaces and their run counts.
- Browse runs inside a workspace.
- Browse events inside a run.
- Filter event types.
- Hide `agent_token` events by default.
- Show short memories.
- Switch between card, list, and drawer-style views.
- Delete workspaces from the memory browser.

Backend API areas:

- Workspace list and run list APIs.
- Event list APIs.
- Context memory query APIs.

### 2.14 Model Management

Models are managed from the home page Models tab.

Current capabilities:

- Configure global default chat model.
- Configure global default multimodal model.
- Configure global default embedding model.
- Create chat model entries.
- Edit chat model entries.
- Delete non-system chat model entries.
- Search chat models.
- Toggle default chat models.
- Track provider, model ID, description, base URL, API key reference,
  context window, max output tokens, temperature, token prices, currency,
  status, and capabilities.
- Chat model capabilities:
  - Vision.
  - Function calling.
  - Streaming.
- Create embedding model entries.
- Edit embedding model entries.
- Delete non-system embedding model entries.
- Search embedding models.
- Toggle default embedding model.
- Track provider, model ID, dimension, distance metric, price, currency,
  status, and created time.

Supported provider patterns include:

- OpenAI-compatible endpoints.
- Alibaba Cloud Model Studio / DashScope style endpoints.
- Ollama.
- Custom provider names.

Backend API areas:

- `/api/llm/chat-models`
- `/api/llm/chat-models/search`
- `/api/llm/chat-models/{model_id}`
- `/api/llm/embedding-models`
- `/api/llm/embedding-models/search`
- `/api/llm/embedding-models/{model_id}`

### 2.15 Task and Artifact APIs

Tasks and artifacts are surfaced in the workspace console and created by
agents/tools during runs.

Task capabilities:

- List workspace tasks.
- Show task title, description, status, assignee, run ID, and result.
- Create/update/delete tasks through built-in tools.

Artifact capabilities:

- List workspace artifacts.
- Get artifact metadata.
- Download artifact content.
- Create/read/update/delete artifacts through built-in tools.

Backend API areas:

- `/api/workspaces/{workspace_id}/tasks`
- `/api/workspaces/{workspace_id}/artifacts`
- `/api/workspaces/{workspace_id}/artifacts/{artifact_id}`
- `/api/workspaces/{workspace_id}/artifacts/{artifact_id}/download`

### 2.16 Event Querying and Real-Time Streaming

Events are the primary durable record of system behavior.

Current capabilities:

- List events by workspace.
- List events by run.
- List events by user.
- Search events.
- Get an event by ID.
- Delete an event.
- Stream run events over SSE.
- Stream workspace events over SSE.
- Get run state.
- Clean up stuck runs.

Backend API areas:

- `/api/events/{event_id}`
- `/api/events/workspace/{workspace_id}/list`
- `/api/events/run/{run_id}/list`
- `/api/events/user/list`
- `/api/events/search`
- `/api/runs/{run_id}/events/stream`
- `/api/workspaces/{workspace_id}/events/stream`
- `/api/runs/{run_id}/events`
- `/api/runs/{run_id}/state`
- `/api/runs/cleanup-stuck`

## 3. Backend Architecture Capabilities

### 3.1 FastAPI Application

The backend is an async FastAPI application. It provides:

- REST APIs for all system resources.
- SSE endpoints for live run and workspace events.
- Health checks.
- Auth middleware.
- Request logging middleware.
- Redis-backed cache middleware.
- CORS support.
- Production mode where documentation can be disabled.

### 3.2 Bootstrap System

The bootstrap layer supports multiple runtime modes:

- API bootstrap.
- Worker bootstrap.
- Celery bootstrap.
- Alembic migration bootstrap.
- MCP service bootstrap.

The bootstrap process initializes:

- Logging.
- Database engines.
- Redis clients.
- Registry system.
- Admin/default tenant data.
- Storage.
- Default context paths.
- Executor and tool metadata sync.

### 3.3 Event-Sourced Runtime

All meaningful state transitions are represented as immutable events.

Current event system capabilities:

- Persist events to PostgreSQL.
- Broadcast events to Redis Streams.
- Maintain per-run and per-workspace event ordering.
- Replay event history for executor context.
- Feed SSE clients in real time.
- Dispatch events to workers.
- Recover or clean up stuck runs.

### 3.4 Run State Machine

Run state is controlled by a dedicated state machine.

Current capabilities:

- Validate legal run status transitions.
- Mark runs running, waiting, finished, failed, or cancelled.
- Persist state changes atomically.
- Trigger async follow-up work such as summarization and context sync.

### 3.5 Registry and Plugin System

The registry system discovers and synchronizes executors, tools, and
structurers.

Current capabilities:

- Decorator-based executor registration.
- Decorator-based tool registration.
- Auto-discovery of plugin modules.
- Database sync for executor templates.
- Database sync for tools.
- Dynamic loading for MCP-imported tools.
- Registry manager for centralized coordination.

Plugin areas:

- `plugins/executors`
- `plugins/tools`
- `plugins/structurers`

Structurers currently include:

- Code structure.
- Document structure.
- File context.
- File structure.
- Knowledge structure.
- Markdown structure.
- Memory structure.
- Run structure.
- Skill structure.
- Stage structure.
- Table structure.
- Tool structure.

### 3.6 Tool Calling

The platform supports multiple tool-calling strategies.

Current capabilities:

- Native function-calling strategy for compatible chat models.
- Prompt/XML-style tool calling strategy for models without native tools.
- Unified response parsing.
- Tool call event generation.
- Tool result and tool error event handling.
- Parallel tool call tracking.
- Human-in-the-loop tool flow through `ask_for_user`.

### 3.7 Worker System

Workers consume events and execute agent logic.

Current capabilities:

- Embedded worker mode inside the API process.
- Standalone worker process mode.
- Multiple worker instances with `structure-worker -n`.
- Redis Streams consumer group coordination.
- Per-workspace stream listening.
- Executor dispatch.
- Tool execution.
- Graceful shutdown.
- Stuck run detection and cleanup support.

### 3.8 Celery Background Tasks

Celery is used for longer-running background work.

Current capabilities:

- Document processing tasks.
- Context sync tasks.
- Knowledge/document indexing follow-up tasks.
- Optional Celery beat and Flower deployment profiles.

### 3.9 MCP Service

The backend includes an internal MCP service.

Current capabilities:

- Expose built-in tools through MCP.
- Run MCP service as part of Docker all-in-one backend mode.
- Probe external MCP servers from the frontend.
- Import selected external MCP tools into the user tool library.

### 3.10 Storage

The system uses S3-compatible object storage for larger content.

Current capabilities:

- Store uploaded documents.
- Store skill files.
- Store artifacts.
- Serve downloads and previews through backend APIs.
- Docker local storage via RustFS.

## 4. Frontend Application Map

Current frontend routes:

- `/login`: login page.
- `/home`: main home page with App, Trigger, Context, and Models tabs.
- `/triggers`: home page opened on Trigger tab.
- `/app`: workspace console.
- `/chat`: app chat route.
- `/knowledge/:knowledgeId/documents`: document management for a knowledge base.
- `/skills/:skillId/files`: skill file browser.
- `/`: redirects to `/app`.

Home page tabs:

- App.
- Trigger.
- Context.
- Models.

Context sub-tabs:

- Knowledge.
- Tool.
- Memory.
- Skill.

Workspace console tabs:

- Runs.
- Context.
- Tasks.
- Results.
- Settings.

Frontend runtime features:

- React 19.
- TypeScript.
- Vite / rolldown-vite.
- Tailwind CSS.
- Radix UI primitives.
- Zustand state stores.
- TanStack Query data fetching.
- SSE streaming client.
- Authenticated Axios API client.
- Dark/light theme toggle.

## 5. Deployment and Operations

### 5.1 Docker Services

The Docker stack includes:

- Backend API, embedded worker, Celery worker, and MCP service.
- Frontend Nginx server.
- PostgreSQL with pgvector.
- Redis.
- RustFS S3-compatible storage.
- Optional Celery beat.
- Optional Flower.
- Optional Redis Commander.
- Optional Ollama.

Primary Docker profiles:

- `infra`
- `app`
- `frontend`
- `postgres`
- `redis`
- `all`

Current local validated port layout:

- Backend API exposed on host `8001`, container `8000`.
- Frontend exposed on host `8082`, container `80`.
- PostgreSQL exposed on host `5433`, container `5432`.
- Redis exposed on host `6380`, container `6379`.
- RustFS exposed on host `9010` and `9011`.
- MCP exposed by the backend container on host `9090`, container `9000`.

### 5.2 Local Commands

Common commands:

- `uv run structure`
- `uv run structure-api`
- `uv run structure-worker`
- `uv run structure-mcp`
- `make start-api`
- `make start-worker`
- `make start-celery`
- `make start-mcp`
- `make start-frontend`
- `make start-all`
- `make stop-all`
- `make db-upgrade`
- `make db-current`
- `make docker-up`
- `make docker-down`

Frontend commands:

- `cd frontend && npm run dev`
- `cd frontend && npm run build`
- `cd frontend && npm run lint`
- `cd frontend && npm run preview`

## 6. Current Verified State

The following functionality was verified locally in Docker on 2026-05-09:

- Frontend production build succeeds.
- Backend Docker image builds successfully.
- Frontend Docker image builds successfully.
- Backend health endpoint returns healthy.
- Frontend health endpoint returns healthy.
- Browser can open `http://127.0.0.1:8082`.
- Login works with the configured local admin credentials.
- Workspace page renders correctly.
- Model page renders correctly.
- Default chat model is configured as an Alibaba Cloud
  OpenAI-compatible chat model.
- Default embedding model is configured as an Alibaba Cloud
  OpenAI-compatible embedding model.
- Browser chat flow works end to end and returns a live model response.
- Browser console had no frontend errors during the verified flow.

## 7. Important Implementation Notes

- The API process reads `STRUCTURE_APP_PORT`; Docker backend containers should
  set it to the internal container port, currently `8000`.
- Host-exposed ports should be treated separately from container-internal
  service ports.
- `.env` may contain local secrets and should not be committed.
- Inner tools can be synchronized by registry discovery and exposed through
  the internal MCP service.
- Document and context processing can involve asynchronous Celery tasks.
- Runs should not hold database transactions while waiting for LLM or tool
  calls.
- Event history is the source of truth for reconstructing agent state.
