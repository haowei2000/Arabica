# Event Worker Architecture

## Overview

The `EventWorker` now implements a **type-based event routing system** that dispatches different event types to specialized handler methods. This replaces the previous monolithic handler that only processed `user.message` events.

## Event Flow

```
Redis Stream → EventWorker.handle_event()
                    │
                    ├─→ Event Type Router
                    │
                    ├─→ user.message          → _handle_user_message()
                    ├─→ user.feedback         → _handle_user_feedback()
                    ├─→ tool.call             → _handle_tool_call()
                    ├─→ tool.client.request   → _handle_tool_client_request()
                    ├─→ run.cancelled         → _handle_run_cancellation()
                    ├─→ task.*                → _handle_task_event()
                    ├─→ artifact.*            → _handle_artifact_event()
                    │
                    ├─→ agent.* (skip - output events)
                    ├─→ tool.result/error (consumed by executors)
                    └─→ unknown → log warning
```

## Event Types & Handlers

### 1. **`user.message`** → `_handle_user_message()`
**Purpose**: Primary agent execution trigger
**What it does**:
- Validates run and executor
- Loads user-defined tools
- Prepares executor instance
- Processes workspace triggers
- Streams agent execution
- Handles state transitions (running → finished/failed/waiting)

**Typical Flow**:
```
User sends message → API creates run → Publishes user.message event
→ Worker picks up event → Executor streams response → Publishes agent.* events
→ Run completes → Worker marks run as finished
```

---

### 2. **`user.feedback`** → `_handle_user_feedback()`
**Purpose**: Handle user ratings/comments on agent responses
**What it does**:
- Stores feedback metadata
- Updates run quality metrics
- Triggers feedback-based workflows (e.g., auto-improve prompts)

**Status**: ⚠️ **Stub implementation** - needs feedback storage service

**Typical Flow**:
```
User rates response → API publishes user.feedback event
→ Worker stores feedback → Analytics pipeline processes feedback
```

---

### 3. **`tool.client.request`** → `_handle_tool_client_request()`
**Purpose**: HITL (Human-in-the-Loop) tool approval requests
**What it does**:
- Logs approval request
- Run should already be in `WAITING` state
- User approves via `/runs/{id}/approve` endpoint
- Approval triggers resume event

**Typical Flow**:
```
Executor needs approval → Raises WaitingForTool exception
→ Worker publishes tool.client.request event
→ SSE streams approval UI to frontend
→ User approves → API publishes resume event → Executor continues
```

---

### 4. **`run.cancelled`** → `_handle_run_cancellation()`
**Purpose**: Cleanup after run cancellation
**What it does**:
- Releases executor resources
- Cancels pending tool calls
- Cleans up temporary files/state

**Status**: ⚠️ **Partial implementation** - needs comprehensive cleanup logic

**Typical Flow**:
```
User cancels run → API updates run status → Publishes run.cancelled event
→ Worker releases resources → Run terminal state reached
```

---

### 5. **`task.*`** → `_handle_task_event()`
**Purpose**: Task lifecycle management
**What it does**:
- Processes task creation, updates, deletion
- Sends notifications to assignees
- Updates workspace task metrics
- Triggers downstream workflows
- Indexes tasks for search

**Status**: ⚠️ **Stub implementation** - needs full task service integration

**Event Types**:
- `task.create` - New task created
- `task.update` - Task modified (status, assignee, etc.)
- `task.delete` - Task deleted/archived
- `task.complete` - Task marked as complete
- `task.assign` - Task assigned to user

**Typical Flow**:
```
Agent creates task via tool → Executor emits task.create event
→ Worker processes event → Update workspace metrics
→ Notify assignee → Index for search
```

---

### 6. **`artifact.*`** → `_handle_artifact_event()`
**Purpose**: Artifact lifecycle management
**What it does**:
- Stores generated artifacts (code, reports, charts)
- Manages artifact versions
- Generates previews/thumbnails
- Indexes artifacts for search
- Notifies workspace members

**Status**: ⚠️ **Stub implementation** - needs storage service integration

**Event Types**:
- `artifact.create` - New artifact generated
- `artifact.update` - Artifact content modified
- `artifact.delete` - Artifact removed
- `artifact.version` - New version created

**Artifact Types**:
- `code` - Source code files (.py, .js, .tsx)
- `report` - Analysis reports (.md, .pdf)
- `chart` - Visualizations (.png, Plotly JSON)
- `data` - Structured data (.csv, .json)
- `document` - Text documents (.md, .txt)

**Typical Flow**:
```
Agent generates code → Executor emits artifact.create event
→ Worker stores to S3 → Generate preview
→ Index metadata → Notify workspace members
```

---

### 7. **Skipped Event Types** (no action needed)

#### Agent Output Events (published BY executors, not consumed):
- `agent.token` - Streaming tokens
- `agent.message` - Final agent messages
- `agent.thinking` - Internal reasoning steps
- `agent.plan.step` - Planning phase steps
- `agent.heartbeat` - Keep-alive signals

#### Tool Events (handled internally by executors):
- `tool.call` - Tool invocation start
- `tool.result` - Tool execution result
- `tool.error` - Tool execution failure
- `tool.pending` - Tool waiting for approval

#### System Events (handled by other services):
- `run.created`, `run.state.change`, `run.completed`, `run.failed`
- `workspace.*` - Workspace lifecycle events
- `system.*` - System-level notifications

---

## Adding New Event Handlers

To add support for a new event type:

1. **Define event type** in `core/enums/events.py`:
   ```python
   class EventType(StrEnum):
       MY_NEW_EVENT = "my_domain.action"
   ```

2. **Add routing** in `EventWorker.handle_event()`:
   ```python
   elif event_type == EventType.MY_NEW_EVENT:
       await self._handle_my_new_event(event)
   ```

3. **Implement handler** in `EventWorker`:
   ```python
   async def _handle_my_new_event(self, event: Event):
       """Handle my_domain.action events - description."""
       try:
           # Validate event
           if not event.run_id:
               logger.warning("Missing run_id")
               return

           # Process event
           # ...

       except Exception as e:
           logger.error(f"_handle_my_new_event error: {e}", exc_info=True)
   ```

---

## Error Handling Strategy

### Per-Handler Errors
Each handler has its own try/except block that:
- Logs error with stack trace
- Does NOT propagate to caller (fail gracefully)
- Allows other events to continue processing

### Top-Level Errors (in `handle_event()`)
If routing itself fails:
- Log error
- Attempt to mark run as failed (if run_id present)
- Event is acknowledged to prevent infinite retries

---

## State Machine Integration

The worker interacts with `RunStateMachine` for state transitions:

| Handler | State Transitions |
|---------|-------------------|
| `_handle_user_message` | `pending → running → finished/failed/waiting` |
| `_handle_tool_client_request` | (no transition - already `waiting`) |
| `_handle_run_cancellation` | (cleanup only - state already `cancelled`) |

---

## Future Enhancements

### High Priority
- [ ] Implement feedback storage service for `_handle_user_feedback()`
- [ ] Add comprehensive cleanup in `_handle_run_cancellation()`
- [ ] Add metrics/telemetry for each event type
- [ ] Implement dead-letter queue for failed events

### Medium Priority
- [ ] Add `run.retry` event handler for automatic retry logic
- [ ] Support `workspace.trigger` events for cross-run orchestration
- [ ] Implement event priority queue (high-priority events first)

### Low Priority
- [ ] Add event replay capability for debugging
- [ ] Implement circuit breaker for failing handlers
- [ ] Add per-event-type rate limiting

---

## Testing

### Unit Tests
Test each handler in isolation:
```python
async def test_handle_user_message():
    worker = EventWorker(...)
    event = Event(event_type="user.message", run_id=uuid4(), ...)
    await worker._handle_user_message(event)
    assert run.status == RunStatus.FINISHED
```

### Integration Tests
Test full event flow:
```python
async def test_user_message_flow():
    # Publish user.message event
    await publisher.publish(event_type="user.message", ...)

    # Wait for worker to process
    await asyncio.sleep(1)

    # Verify agent.message event was published
    events = await get_events(run_id)
    assert any(e.event_type == "agent.message" for e in events)
```

---

## Observability

### Logs
Each handler logs:
- **INFO**: Event received, processing started/completed
- **WARNING**: Validation failures, missing data
- **ERROR**: Handler exceptions, state machine failures

### Metrics (TODO)
- Event processing duration by type
- Event failure rate by type
- Queue depth per event type
- Executor instantiation time

---

## Related Files

- **Event Types**: `src/aiwen/core/enums/events.py`
- **Event Publisher**: `src/aiwen/services/events/event_publisher.py`
- **State Machine**: `src/aiwen/services/runs/run_state_machine.py`
- **Run API**: `src/aiwen/routers/runs/runs.py`
- **Executor Base**: `src/aiwen/core/interfaces/executor.py`
