# Context Sync: Entity-to-Context Mapping

This document analyzes how each entity type (Tool, Skill, Knowledge, Run, Trigger)
is transformed into `Context` table entries, including field mappings, sync paths,
embedding generation, and workspace propagation.

---

## Overview

There are **two sync paths** that work in tandem:

| Path | Class / Module | When | Embedding | Workspace Propagation |
|------|---------------|------|-----------|----------------------|
| **ContextSyncer** | `services/context/context_syncer.py` | Inline, during API CRUD | No | No |
| **Celery Tasks** | `celery_worker/tasks/context_sync_tasks.py` | Background, after commit | Yes | Yes (`scope=WORKSPACE`) |

### ContextSyncer

Called synchronously inside CRUD service methods (create / update / delete).
Writes a single `scope=USER` `Context` row immediately, within the same DB session.
No embeddings, no workspace fan-out.

```
API Request
  └─ CRUD service
       └─ ContextSyncer._upsert()  ──► Context (scope=USER)
```

### Celery Tasks

Dispatched after the CRUD commit. Run in background workers.
Responsibilities:
1. Re-upsert the global `scope=USER` Context row (with richer content).
2. Generate a vector embedding and write it back.
3. Fan-out to every workspace the user owns as `scope=WORKSPACE` entries
   (via `workspace_context_sync._sync_path_to_workspaces`).

```
Celery Worker
  ├─ _upsert_context()        ──► Context (scope=USER)
  ├─ _generate_embedding()    ──► vector (blocking HTTP, outside session)
  ├─ _store_embedding()       ──► Context.embedding_1024
  └─ _sync_path_to_workspaces()
       └─ for each workspace  ──► Context (scope=WORKSPACE, source_id=workspace_id)
```

---

## Path Conventions

```
/tools/{tool_name}
/skills/{skill_name}
/knowledge/{knowledge_name}
/triggers/{trigger_name}
/workspaces/{workspace_name}
/workspaces/{workspace_name}/runs/{run_id[:8]}
```

---

## Entity Mappings

### Tool

Two sub-types behave differently:

| | External Tool (`tool_type="external"`) | Inner Tool (`tool_type="inner"`) |
|-|----------------------------------------|----------------------------------|
| Owner | `tool.user_id` | None (system-wide) |
| ContextSyncer | Yes (skips if no user_id) | No |
| Celery task | `sync_tool_to_contexts` | `sync_inner_tool_to_contexts` |
| Workspace fan-out | User's workspaces only | ALL active workspaces |

**Context fields:**

| Context field | Value |
|---------------|-------|
| `path` | `/tools/{tool.name}` |
| `context_type` | `tool` |
| `source_id` | `tool.id` |
| `scope` | `USER` (global row) / `WORKSPACE` (fan-out) |
| `glance` | `"{display_name} — {description[:60]}"` |
| `summary` | `tool.description` |
| `content` | Full OpenAI function-calling schema JSON (Celery) or structured text (ContextSyncer) |
| `tags` | `["tool", ...tool.tags]` |
| `meta` | `{tool_id, tool_code, tool_type, enabled}` |

**Content format (Celery):**
```json
{
  "type": "function",
  "function": {
    "name": "tool_code",
    "description": "...",
    "parameters": { /* input_schema */ }
  }
}
```

**Embed text:** `display_name + description + schema_str`

---

### Skill

| Context field | Value |
|---------------|-------|
| `path` | `/skills/{skill.name}` |
| `context_type` | `SKILL` |
| `source_id` | `skill.id` |
| `glance` | `skill.glance` or `"Skill: {name}"` |
| `summary` | `skill.description` or `skill.summary` |
| `content` | `skill.content` (Celery) / structured text (ContextSyncer) |
| `tags` | `["skill", "skills", ...skill.tags]` |
| `meta` | `{skill_id}` |

**Embed text:** `skill.content` or `glance`

---

### Knowledge

| Context field | Value |
|---------------|-------|
| `path` | `/knowledge/{knowledge.name}` |
| `context_type` | `knowledge` |
| `source_id` | `knowledge.id` |
| `glance` | `"Knowledge: {name} — {description[:60]}"` |
| `summary` | `knowledge.description` |
| `content` | `knowledge.description` (Celery) / structured text (ContextSyncer) |
| `tags` | `["knowledge", ...knowledge.tags]` |
| `meta` | `{knowledge_id, status, provider}` |

**Embed text:** `name + description`

---

### Trigger (WorkspaceTrigger)

Triggers are synced **only via ContextSyncer** — there is no Celery task.
No embedding is generated. No workspace fan-out.

| Context field | Value |
|---------------|-------|
| `path` | `/triggers/{trigger.name}` |
| `context_type` | `trigger` |
| `source_id` | `trigger.id` |
| `glance` | `"Trigger: {name} — {description[:50]}"` |
| `summary` | `trigger.description` |
| `content` | Name + Description + Event + Condition + Action (tool name) |
| `tags` | `["trigger", event_type]` |
| `meta` | `{workspace_id, event_type, tool_name, enabled}` |

**Content format:**
```
Trigger: {name}
Description: {description}
Event: {event_type}
Condition: {condition_type}
Action (tool): {tool_name}
```

> **Note:** Triggers are workspace-scoped by nature (`workspace_id` in meta),
> but only a single `scope=USER` row is written. The trigger content is available
> to agents reading the user's context tree.

---

### Run

Two Celery tasks handle runs:

| Task | Purpose |
|------|---------|
| `sync_run_to_contexts` | Stores user input + agent output as a conversation record |
| `sync_run_events_to_context` | Aggregates the full event timeline into a searchable entry |

**`sync_run_to_contexts` — only fires for terminal runs** (`finished` / `failed` / `cancelled`):

| Context field | Value |
|---------------|-------|
| `path` | `/workspaces/{workspace_name}/runs/{run_id[:8]}` (ContextSyncer) |
| `context_type` | `run` |
| `source_id` | `run.id` |
| `glance` | `input_msg[:80]` or `"Run {run_id[:8]}"` |
| `summary` | `"[{status}] {input_msg[:200]}"` |
| `content` | `"User: {input}\n\nAssistant: {output}"` |
| `tags` | `["run", status]` |
| `meta` | `{run_id, workspace_id, status, app_id, trigger_type}` |

**`sync_run_events_to_context`** — formats each non-noise event as a timeline line:

| Context field | Value |
|---------------|-------|
| `context_type` | `run_events` |
| `source_id` | `run.id` |
| `glance` | `"{N} events — run {run_id[:8]}"` |
| `summary` | `"{first_event_type} → {last_event_type} ({N} events)"` |
| `content` | `"[001] event_type: ...\n[002] ..."` (capped at 2000 chars for embedding) |
| `tags` | `["events", "run"]` |

Noise events filtered out: `agent.token`, `agent_token`

**Embed text (both tasks):** content, capped at 2000 characters

---

## Embedding

All embeddings are generated **outside any active DB session** to avoid blocking
the event loop during the synchronous HTTP call to the embedding provider.

```
Default provider : tongyi
Default model    : text-embedding-v3
Default dimension: 1024  (stored in Context.embedding_1024)
```

The dimension determines which column is written (`embedding_384`, `embedding_768`,
`embedding_1024`, or `embedding_1536`). When a Context row is updated, all four
embedding columns are cleared so the new embedding is regenerated cleanly.

---

## Workspace Propagation (`scope=WORKSPACE`)

After the global `scope=USER` row is written, `_sync_path_to_workspaces` fans out
to every active workspace owned by the user (or all workspaces for inner tools).

Each fan-out entry:
- `Context.scope = WORKSPACE`
- `Context.source_id = workspace_id`
- `Context.path` — same path as the user-scoped row
- `Context.meta["workspace_id"]` — the workspace UUID string
- After write: Redis dirty-flag `workspace_context_dirty:{workspace_id}` is set (TTL 600 s)
  so the `WorkspaceContextService` in-memory cache reloads on next access.

**Which entities fan out:**

| Entity | Fan-out |
|--------|---------|
| Tool (external) | User's workspaces |
| Tool (inner) | All active workspaces |
| Skill | User's workspaces |
| Knowledge | User's workspaces |
| Trigger | None |
| Run | None |
| Workspace | None |

---

## Delete Behavior

| Path | Behavior |
|------|----------|
| ContextSyncer | Hard-delete matching `(user_id, path)` row immediately |
| Celery `delete_resource_contexts` | Hard-delete `scope=USER` rows by `(source_id, context_type)` + hard-delete `scope=WORKSPACE` rows by `meta[meta_key]`, then invalidate caches |

---

## Summary Table

| Entity | ContextSyncer | Celery Task | Embedding | Workspace Scope |
|--------|:---:|:---:|:---:|:---:|
| Tool (external) | Yes | `sync_tool_to_contexts` | Yes | User's workspaces |
| Tool (inner) | No | `sync_inner_tool_to_contexts` | Yes | All workspaces |
| Skill | Yes | `sync_skill_to_contexts` | Yes | User's workspaces |
| Knowledge | Yes | `sync_knowledge_to_contexts` | Yes | User's workspaces |
| Trigger | Yes | None | No | None |
| Run | Yes (lightweight) | `sync_run_to_contexts` | Yes | None |
| Run Events | No | `sync_run_events_to_context` | Yes | None |
| Workspace | Yes | `sync_workspace_to_contexts` | Yes | None |
