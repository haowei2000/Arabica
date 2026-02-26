# Tool Execution Architecture (Worker-Based)

## Overview

The tool execution logic has been **decoupled from executors** and moved to the **EventWorker**. This creates a clear separation of concerns:

- **Executors**: Focus on LLM interaction and conversation management
- **EventWorker**: Handles tool discovery, execution, monitoring, and result delivery

## Architecture Comparison

### OLD: Executor-Based Tool Execution ❌

```
Executor.stream()
    │
    ├─→ LLM returns tool calls
    ├─→ Executor._process_tool_calls()
    │     ├─→ Check HITL approval
    │     ├─→ Execute tool directly via ToolCaller
    │     ├─→ Emit tool.result event
    │     └─→ Append result to message history
    └─→ Continue agentic loop
```

**Problems:**
- Tool execution blocks executor
- No centralized tool monitoring
- Hard to scale tool execution independently
- Difficult to retry failed tools without re-running LLM
- HITL approval tightly coupled to executor

---

### NEW: Worker-Based Tool Execution ✅

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   Executor   │      │ EventWorker  │      │   Database   │
│              │      │              │      │ + Redis      │
└──────┬───────┘      └──────┬───────┘      └──────┬───────┘
       │                     │                     │
       │ LLM requests tools  │                     │
       │─────────────────────┼────────────────────>│
       │  emit tool.call     │                     │ store event
       │                     │                     │
       │ pause (WaitingForTool)                    │
       │ waiting_type: "tool_execution"            │
       │                     │                     │
       │      tool.call event│<────────────────────│ consume
       │                     │                     │
       │                     │ execute tool        │
       │                     │ (via ToolCaller)    │
       │                     │                     │
       │                     │ publish result      │
       │                     │────────────────────>│ store
       │                     │ tool.result event   │
       │                     │                     │
       │                     │ check all tools done│
       │                     │                     │
       │                     │ resume run          │
       │<────────────────────│ (with tool results) │
       │                     │                     │
       │ collect results from DB                   │
       │────────────────────────────────────────────>
       │                     │                     │
       │ append to messages  │                     │
       │ continue agentic loop                     │
```

---

## Component Responsibilities

### 1. Executor (`DefaultExecutor`)

#### **`_process_tool_calls()`** (Modified)
```python
async def _process_tool_calls(
    self,
    tool_calls: list[ToolCallRequest],
    messages: list[ChatMessage]
) -> AsyncGenerator[AgentEvent, None]:
    """Emit tool.call events and pause for worker execution."""

    # Emit tool.call events for each tool
    for tc in tool_calls:
        yield self._emit_tool_call(
            tool_name=tc.name,
            tool_id=tc.id,
            arguments=tc.arguments,
        )

    # Pause execution - worker will handle tools
    raise WaitingForTool({
        "type": "tool_execution",
        "pending_tool_calls": [...],
        "messages": self._serialize_messages(messages),
        "executor_code": self.TEMPLATE["executor_code"],
    })
```

**Key Changes:**
- ✅ NO direct tool execution
- ✅ Emits `tool.call` events only
- ✅ Pauses with `type: "tool_execution"`
- ✅ Stores conversation state for resume

---

#### **`_handle_resume()`** (Modified)
```python
async def _handle_resume(
    self,
    user_message: dict
) -> AsyncGenerator[AgentEvent, None]:
    """Resume after worker completes tool execution."""

    waiting_info = user_message.get("_waiting_info", {})
    waiting_type = waiting_info.get("type")

    if waiting_type == "tool_execution":
        # NEW: Tool results come from worker
        tool_results = user_message.get("_tool_results", [])

        # Append results to message history
        for tr in tool_results:
            if tr["success"]:
                content = json.dumps(tr["result"])
            else:
                content = json.dumps({"error": tr["error_message"]})

            messages.append(ChatMessage(
                role="tool",
                content=content,
                tool_call_id=tr["tool_id"],
            ))

    # Continue agentic loop with tools executed
    async for event in self._agentic_loop(messages):
        yield event
```

**Key Changes:**
- ✅ Reads tool results from `_tool_results` key
- ✅ NO tool execution
- ✅ Directly continues conversation

---

### 2. EventWorker

#### **`handle_event()`** (Routing)
```python
async def handle_event(self, event: Event):
    """Route events to specialized handlers."""

    if event_type == EventType.TOOL_CALL:
        await self._handle_tool_call(event)  # NEW
    elif event_type == EventType.USER_MESSAGE:
        await self._handle_user_message(event)
    # ... other handlers
```

---

#### **`_handle_tool_call()`** (NEW)
```python
async def _handle_tool_call(self, event: Event):
    """Execute tool and publish result/error event."""

    run_id = event.run_id
    payload = event.payload
    tool_name = payload["tool_name"]
    tool_id = payload["tool_id"]
    arguments = payload["arguments"]

    # Check HITL approval requirement
    if await self._tool_requires_approval(run, tool_name):
        # Pause for approval
        await self.state_machine.pause_for_tool(run_id, ...)
        await self.event_publisher.publish(
            event_type=EventType.TOOL_PENDING, ...
        )
        return

    # Execute tool
    try:
        result = await self._tool_caller.call(tool_name, arguments)

        # Publish success
        await self.event_publisher.publish(
            event_type=EventType.TOOL_RESULT,
            run_id=str(run_id),
            payload={
                "tool_id": tool_id,
                "tool_name": tool_name,
                "result": result,
                "execution_time_ms": elapsed_ms,
            },
        )
    except Exception as e:
        # Publish error
        await self.event_publisher.publish(
            event_type=EventType.TOOL_ERROR,
            run_id=str(run_id),
            payload={
                "tool_id": tool_id,
                "tool_name": tool_name,
                "error_message": str(e),
            },
        )
```

**Responsibilities:**
- ✅ Execute tools via `ToolCaller`
- ✅ Check HITL approval requirements
- ✅ Publish `tool.result` or `tool.error` events
- ✅ Centralized error handling and logging

---

#### **`_prepare_resume_data()`** (Modified)
```python
async def _prepare_resume_data(
    self,
    run_id: UUID,
    waiting_for: dict,
    user_message: dict
) -> dict:
    """Collect tool results from events and package for executor."""

    waiting_type = waiting_for.get("type")

    if waiting_type == "tool_execution":
        # NEW: Query database for tool result events
        pending_calls = waiting_for.get("pending_tool_calls", [])
        tool_results = await self._collect_tool_results(run_id, pending_calls)

        return {
            **user_message,
            "_resumed": True,
            "_waiting_info": waiting_for,
            "_tool_results": tool_results,  # NEW
        }
```

**Responsibilities:**
- ✅ Query `tool.result`/`tool.error` events from database
- ✅ Match results to pending tool calls by `tool_id`
- ✅ Package results for executor resume

---

#### **`_collect_tool_results()`** (NEW)
```python
async def _collect_tool_results(
    self,
    run_id: UUID,
    pending_calls: list[dict]
) -> list[dict]:
    """Query tool result events from database."""

    tool_ids = [tc["id"] for tc in pending_calls]

    # Query events
    stmt = select(Event).where(
        Event.run_id == run_id,
        Event.event_type.in_([
            EventType.TOOL_RESULT,
            EventType.TOOL_ERROR,
        ])
    )

    events = await self.db.execute(stmt)

    # Parse results
    tool_results = []
    for event in events:
        payload = event.payload
        if event.event_type == EventType.TOOL_RESULT:
            tool_results.append({
                "tool_id": payload["tool_id"],
                "tool_name": payload["tool_name"],
                "success": True,
                "result": payload["result"],
            })
        elif event.event_type == EventType.TOOL_ERROR:
            tool_results.append({
                "tool_id": payload["tool_id"],
                "tool_name": payload["tool_name"],
                "success": False,
                "error_message": payload["error_message"],
            })

    return tool_results
```

---

## Event Flow

### Phase 1: Tool Call Emission (Executor)

```
User: "What's the weather in SF?"
  ↓
Executor calls LLM
  ↓
LLM response: tool_use(name="get_weather", args={"city": "SF"})
  ↓
Executor._process_tool_calls()
  ├─→ Emit: tool.call(tool_name="get_weather", tool_id="abc123", args={...})
  └─→ Raise: WaitingForTool(type="tool_execution", pending=[{...}])
      ↓
    Run transitions to WAITING state
```

---

### Phase 2: Tool Execution (Worker)

```
EventWorker consumes tool.call event
  ↓
_handle_tool_call()
  ├─→ Check HITL approval: NO
  ├─→ Execute: await tool_caller.call("get_weather", {"city": "SF"})
  ├─→ Success: result = {"temperature": 72, "conditions": "sunny"}
  └─→ Publish: tool.result(tool_id="abc123", result={...})
      ↓
    Event stored in database
```

---

### Phase 3: Resume (Worker)

```
Worker checks: All tools complete? YES
  ↓
_prepare_resume_data()
  ├─→ Query tool.result events from DB
  ├─→ Match by tool_id: abc123
  └─→ Package: _tool_results=[{tool_id, success=True, result={...}}]
      ↓
Resume executor with tool results
  ↓
Executor._handle_resume()
  ├─→ Read _tool_results
  ├─→ Append to messages: ChatMessage(role="tool", content="{...}")
  └─→ Continue _agentic_loop()
      ↓
    LLM generates final response
```

---

## Benefits

### 1. **Separation of Concerns**
- Executors: LLM conversation logic
- Worker: Tool orchestration and execution
- Clear, testable boundaries

### 2. **Independent Scaling**
- Scale executors for LLM throughput
- Scale workers for tool execution
- Different resource requirements

### 3. **Centralized Tool Management**
- Single place for tool execution logic
- Unified logging and monitoring
- Easy to add cross-cutting concerns (rate limiting, caching)

### 4. **Resilience**
- Tool failures don't crash executors
- Worker can retry failed tools
- Executor state preserved across tool execution

### 5. **HITL Flexibility**
- Approval logic in worker, not executor
- Easy to change approval policies
- Can batch approve multiple tools

### 6. **Debugging**
- Tool.call/result events in database
- Full audit trail of tool executions
- Replay tool execution without re-running LLM

---

## Migration Notes

### Backward Compatibility

The new architecture maintains backward compatibility for HITL approval:

```python
# LEGACY path: tool_approval waiting type
if waiting_type == "tool_approval":
    # User approves via /runs/{id}/approve
    # Executor re-emits tool.call event
    # Worker executes and publishes result
    # Executor resumes with result
```

### Breaking Changes

**None** - The old `_execute_tool_call()` method is still present but unused. Executors can be gradually migrated.

---

## Future Enhancements

### High Priority
- [ ] Tool execution retries with exponential backoff
- [ ] Tool execution timeouts
- [ ] Parallel tool execution (emit all, wait for all)
- [ ] Tool result caching

### Medium Priority
- [ ] Tool execution metrics (latency, success rate)
- [ ] Tool execution quotas per user/workspace
- [ ] Async tool execution (long-running tools)
- [ ] Tool chaining without LLM round-trip

### Low Priority
- [ ] Tool execution sandboxing
- [ ] Tool execution distributed tracing
- [ ] Tool result compression for large outputs

---

## Testing

### Unit Tests

```python
async def test_handle_tool_call():
    worker = EventWorker(...)
    event = Event(
        event_type="tool.call",
        run_id=run_id,
        payload={
            "tool_name": "get_weather",
            "tool_id": "abc123",
            "arguments": {"city": "SF"},
        },
    )

    await worker._handle_tool_call(event)

    # Verify tool.result event published
    result_events = await get_events(run_id, event_type="tool.result")
    assert len(result_events) == 1
    assert result_events[0].payload["tool_name"] == "get_weather"
```

### Integration Tests

```python
async def test_full_tool_execution_flow():
    # User sends message
    await api.post("/runs", json={"message": "What's the weather?"})

    # Wait for tool.call event
    await wait_for_event(run_id, "tool.call")

    # Worker processes tool
    # (automatic via worker polling)

    # Wait for tool.result event
    await wait_for_event(run_id, "tool.result")

    # Wait for agent response
    await wait_for_event(run_id, "agent.message")

    # Verify final message includes weather data
    final_message = await get_final_message(run_id)
    assert "72°" in final_message
```

---

## Related Files

- **Executor**: `src/aiwen/plugins/executors/simple/concrete.py`
- **EventWorker**: `src/aiwen/services/events/event_worker.py`
- **Event Types**: `src/aiwen/core/enums/events.py`
- **ToolCaller**: `src/aiwen/registries/tool_service.py`
- **State Machine**: `src/aiwen/services/runs/run_state_machine.py`
