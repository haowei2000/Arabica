# Task and Artifact Event Types

## Overview

The system now supports **task management** and **artifact tracking** via dedicated event types. These events enable:

- Task lifecycle management (create, update, delete)
- Artifact versioning and storage
- Notification and workflow triggers
- Audit trail and history tracking

---

## Event Types

### Task Events

| Event Type | Description | When Published |
|------------|-------------|----------------|
| `task.create` | New task created | Agent/user creates a task via tool or API |
| `task.update` | Task modified | Task status, assignee, description, or metadata changes |
| `task.delete` | Task deleted/archived | Task removed by user or automated cleanup |
| `task.complete` | Task marked as complete | Task reaches terminal state (completed/cancelled) |
| `task.assign` | Task assigned to user | Task assignee changes |

### Artifact Events

| Event Type | Description | When Published |
|------------|-------------|----------------|
| `artifact.create` | New artifact generated | Agent creates code, report, chart, or data export |
| `artifact.update` | Artifact content modified | Artifact content or metadata updated |
| `artifact.delete` | Artifact removed | Artifact deleted or archived |
| `artifact.version` | New artifact version | New version of existing artifact created |

---

## Task Events

### 1. `task.create`

**Purpose**: Track task creation for downstream workflows and notifications

**Payload Schema**:
```json
{
  "task": {
    "id": "uuid",
    "title": "Implement user authentication",
    "description": "Add JWT-based auth to API endpoints",
    "status": "pending",
    "priority": "high",
    "assignee_id": "uuid | null",
    "workspace_id": "uuid",
    "created_by": "uuid",
    "due_date": "2026-03-01T00:00:00Z",
    "tags": ["auth", "backend"],
    "metadata": {
      "estimated_hours": 8,
      "complexity": "medium"
    }
  },
  "source": "agent" | "user" | "trigger"
}
```

**Event Flow**:
```
Agent creates task via create_task tool
  ↓
Executor emits task.create event
  ↓
EventWorker processes event
  ├─→ Send notification to assignee
  ├─→ Update workspace task count
  ├─→ Trigger downstream workflows
  └─→ Index task for search
```

**Use Cases**:
- Agent creates tasks during planning phase
- User manually creates tasks via UI
- Workspace trigger auto-creates tasks on conditions

---

### 2. `task.update`

**Purpose**: Track task state changes for notifications and history

**Payload Schema**:
```json
{
  "task_id": "uuid",
  "changes": {
    "status": {
      "old": "pending",
      "new": "in_progress"
    },
    "assignee_id": {
      "old": null,
      "new": "uuid"
    },
    "priority": {
      "old": "medium",
      "new": "high"
    }
  },
  "updated_by": "uuid",
  "update_reason": "Agent completed dependency task"
}
```

**Event Flow**:
```
Task status changes: pending → in_progress
  ↓
API/Agent publishes task.update event
  ↓
EventWorker processes event
  ├─→ Notify assignee of status change
  ├─→ Update dependent tasks
  ├─→ Track task history/timeline
  └─→ Trigger status-based workflows
```

**Use Cases**:
- Task status progression tracking
- Assignee change notifications
- Task priority escalation
- Dependency resolution

---

### 3. `task.delete`

**Purpose**: Track task deletion for cleanup and audit

**Payload Schema**:
```json
{
  "task_id": "uuid",
  "task": {
    "title": "...",
    "status": "cancelled"
  },
  "deleted_by": "uuid",
  "delete_reason": "Duplicate task",
  "soft_delete": true
}
```

**Event Flow**:
```
User deletes task
  ↓
API publishes task.delete event
  ↓
EventWorker processes event
  ├─→ Archive task (soft delete)
  ├─→ Notify stakeholders
  ├─→ Update workspace metrics
  └─→ Clean up dependencies
```

**Use Cases**:
- Task cleanup and archival
- Audit trail for deleted tasks
- Cascade deletion of subtasks

---

### 4. `task.complete` (NEW)

**Purpose**: Celebrate task completion, trigger follow-up actions

**Payload Schema**:
```json
{
  "task_id": "uuid",
  "task": {
    "title": "...",
    "status": "completed"
  },
  "completed_by": "uuid",
  "completion_time": "2026-02-26T12:00:00Z",
  "completion_notes": "All tests passing"
}
```

**Use Cases**:
- Trigger dependent tasks
- Send completion notifications
- Update project progress metrics
- Generate completion reports

---

### 5. `task.assign` (NEW)

**Purpose**: Track task assignment for workload balancing

**Payload Schema**:
```json
{
  "task_id": "uuid",
  "assignee_id": "uuid",
  "previous_assignee_id": "uuid | null",
  "assigned_by": "uuid",
  "assignment_reason": "Best suited for this task"
}
```

**Use Cases**:
- Notify new assignee
- Update workload dashboards
- Track assignment history

---

## Artifact Events

### 1. `artifact.create`

**Purpose**: Track generated artifacts for storage and indexing

**Payload Schema**:
```json
{
  "artifact": {
    "id": "uuid",
    "type": "code" | "report" | "chart" | "data" | "document",
    "name": "authentication_service.py",
    "description": "JWT authentication service implementation",
    "content_type": "text/x-python",
    "content": "...", // inline for small artifacts
    "s3_key": "artifacts/workspace-id/run-id/artifact-id.py", // for large artifacts
    "size_bytes": 4096,
    "metadata": {
      "language": "python",
      "framework": "fastapi",
      "version": 1
    },
    "run_id": "uuid",
    "workspace_id": "uuid",
    "created_by": "uuid"
  },
  "preview": {
    "thumbnail_url": "https://...",
    "excerpt": "First 500 chars of content..."
  }
}
```

**Event Flow**:
```
Agent generates code file
  ↓
Executor emits artifact.create event
  ↓
EventWorker processes event
  ├─→ Store content to S3
  ├─→ Generate syntax-highlighted preview
  ├─→ Index metadata for search
  ├─→ Notify workspace members
  └─→ Link artifact to run
```

**Artifact Types**:

| Type | Description | Examples |
|------|-------------|----------|
| `code` | Source code files | `.py`, `.js`, `.tsx`, `.go` |
| `report` | Analysis reports | `.md`, `.pdf`, `.html` |
| `chart` | Visualizations | `.png`, `.svg`, Plotly JSON |
| `data` | Structured data | `.csv`, `.json`, `.parquet` |
| `document` | Text documents | `.md`, `.txt`, `.docx` |

---

### 2. `artifact.update`

**Purpose**: Track artifact modifications and versioning

**Payload Schema**:
```json
{
  "artifact_id": "uuid",
  "changes": {
    "content": {
      "old_hash": "sha256:abc123...",
      "new_hash": "sha256:def456..."
    },
    "metadata": {
      "version": {
        "old": 1,
        "new": 2
      }
    }
  },
  "updated_by": "uuid",
  "update_reason": "Fixed bug in authentication logic"
}
```

**Event Flow**:
```
User edits artifact content
  ↓
API publishes artifact.update event
  ↓
EventWorker processes event
  ├─→ Create new version
  ├─→ Update search index
  ├─→ Invalidate cached previews
  └─→ Notify collaborators
```

---

### 3. `artifact.delete`

**Purpose**: Track artifact deletion for cleanup

**Payload Schema**:
```json
{
  "artifact_id": "uuid",
  "deleted_by": "uuid",
  "delete_reason": "Outdated implementation",
  "soft_delete": true,
  "cleanup_storage": true
}
```

**Event Flow**:
```
User deletes artifact
  ↓
API publishes artifact.delete event
  ↓
EventWorker processes event
  ├─→ Soft delete (archive)
  ├─→ Clean up S3 storage
  ├─→ Remove from search index
  └─→ Notify references
```

---

### 4. `artifact.version`

**Purpose**: Track artifact versions for diff and rollback

**Payload Schema**:
```json
{
  "artifact_id": "uuid",
  "version": 3,
  "previous_version": 2,
  "version_metadata": {
    "commit_message": "Refactored error handling",
    "author": "uuid",
    "timestamp": "2026-02-26T12:00:00Z"
  },
  "diff": {
    "additions": 15,
    "deletions": 8,
    "changes": 3
  }
}
```

**Use Cases**:
- Track artifact evolution over time
- Enable rollback to previous versions
- Compare versions side-by-side
- Generate change history

---

## Integration Examples

### Example 1: Agent Creates Tasks During Planning

```python
# In executor (during planning phase)
tasks = [
    {"title": "Set up database", "priority": "high"},
    {"title": "Implement API endpoints", "priority": "medium"},
    {"title": "Write tests", "priority": "medium"},
]

for task in tasks:
    # Emit task.create event
    yield AgentEvent(
        event_type=EventType.TASK_CREATE,
        payload={
            "task": {
                "id": str(uuid4()),
                "title": task["title"],
                "priority": task["priority"],
                "status": "pending",
                "workspace_id": self.workspace_id,
                "created_by": self.user_id,
            },
            "source": "agent",
        }
    )
```

---

### Example 2: Agent Generates Code Artifact

```python
# In executor (after code generation)
code_content = """
def authenticate(username: str, password: str) -> Token:
    # JWT authentication logic
    ...
"""

yield AgentEvent(
    event_type=EventType.ARTIFACT_CREATE,
    payload={
        "artifact": {
            "id": str(uuid4()),
            "type": "code",
            "name": "authenticate.py",
            "content_type": "text/x-python",
            "content": code_content,
            "size_bytes": len(code_content.encode()),
            "metadata": {
                "language": "python",
                "framework": "fastapi",
                "version": 1,
            },
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "created_by": self.user_id,
        }
    }
)
```

---

### Example 3: Task Update Workflow

```python
# In task service
async def update_task_status(task_id: UUID, new_status: str, user_id: UUID):
    # Update database
    task = await task_crud.update(task_id, {"status": new_status})

    # Publish event
    await event_publisher.publish(
        event_type=EventType.TASK_UPDATE,
        workspace_id=str(task.workspace_id),
        run_id=str(task.run_id) if task.run_id else None,
        payload={
            "task_id": str(task_id),
            "changes": {
                "status": {
                    "old": task.previous_status,
                    "new": new_status,
                }
            },
            "updated_by": str(user_id),
        },
        auto_commit=True,
    )
```

---

## Event Worker Side Effects

### Task Events

| Event | Side Effects |
|-------|--------------|
| `task.create` | - Send email/Slack notification to assignee<br>- Increment workspace task count<br>- Index task in search engine<br>- Trigger dependent workflows |
| `task.update` | - Notify assignee of changes<br>- Update dependent tasks<br>- Log task history<br>- Trigger status-based automations |
| `task.delete` | - Archive task (soft delete)<br>- Notify stakeholders<br>- Update workspace metrics<br>- Clean up subtasks |

### Artifact Events

| Event | Side Effects |
|-------|--------------|
| `artifact.create` | - Store content to S3<br>- Generate preview/thumbnail<br>- Index metadata for search<br>- Notify workspace members<br>- Run post-processing (syntax highlighting) |
| `artifact.update` | - Create new version<br>- Update search index<br>- Invalidate cached previews<br>- Notify collaborators |
| `artifact.delete` | - Soft delete/archive<br>- Clean up S3 storage<br>- Remove from search index<br>- Notify references |

---

## Future Enhancements

### High Priority
- [ ] Task dependency graph visualization
- [ ] Artifact diff view (compare versions)
- [ ] Task template system
- [ ] Artifact storage optimization (compression)

### Medium Priority
- [ ] Task time tracking integration
- [ ] Artifact collaboration (comments, reviews)
- [ ] Task batch operations
- [ ] Artifact search with content indexing

### Low Priority
- [ ] Task Gantt chart view
- [ ] Artifact export to external systems
- [ ] Task recurring schedules
- [ ] Artifact access control (permissions)

---

## Related Files

- **Event Types**: `src/structure/core/enums/events.py`
- **Event Worker**: `src/structure/services/events/event_worker.py`
- **Event Publisher**: `src/structure/services/events/event_publisher.py`
- **Executor Base**: `src/structure/core/interfaces/executor.py`
