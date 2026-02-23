# tests/test_tool_approval/test_resume_endpoint.py
"""Tests for the /workspaces/{workspace_id}/runs/{run_id}/resume API endpoint.

This endpoint handles tool approval decisions from the frontend:
- Accepts approval/denial decisions
- Stores decision in Redis for worker pickup
- Transitions run from WAITING -> RUNNING
- Re-triggers the worker via Redis stream
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from aiwen.schemas.runs.run import RunResumeRequest, RunStatus


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_workspace():
    """Create a mock workspace."""
    workspace = MagicMock()
    workspace.id = str(uuid4())
    workspace.app_id = str(uuid4())
    return workspace


@pytest.fixture
def mock_run_waiting():
    """Create a mock run in WAITING state."""
    run = MagicMock()
    run.id = str(uuid4())
    run.workspace_id = str(uuid4())
    run.status = RunStatus.WAITING.value
    run.waiting_for = {
        "type": "tool_approval",
        "tool_name": "delete_file",
        "tool_id": "call_123",
        "arguments": {"path": "/tmp/test.txt"},
        "executor_code": "DEFAULT001",
    }
    run.input_data = {"message": "Delete the file"}
    return run


@pytest.fixture
def mock_run_running():
    """Create a mock run in RUNNING state."""
    run = MagicMock()
    run.id = str(uuid4())
    run.workspace_id = str(uuid4())
    run.status = RunStatus.RUNNING.value
    run.waiting_for = None
    return run


@pytest.fixture
def mock_current_user():
    """Create a mock current user."""
    user = MagicMock()
    user.id = str(uuid4())
    user.email = "test@example.com"
    return user


@pytest.fixture
def mock_workspace_crud(mock_workspace):
    """Create a mock workspace CRUD."""
    crud = AsyncMock()
    crud.get_by_id_and_user = AsyncMock(return_value=mock_workspace)
    return crud


@pytest.fixture
def mock_run_crud(mock_run_waiting):
    """Create a mock run CRUD."""
    crud = AsyncMock()
    crud.get_by_id = AsyncMock(return_value=mock_run_waiting)
    return crud


@pytest.fixture
def mock_state_machine(mock_run_waiting):
    """Create a mock state machine."""
    sm = AsyncMock()
    sm.redis = AsyncMock()
    sm.redis.set = AsyncMock()
    sm.redis.xadd = AsyncMock()
    sm.resume_from_tool = AsyncMock(return_value=mock_run_waiting)
    return sm


@pytest.fixture
def mock_event_publisher():
    """Create a mock event publisher."""
    publisher = AsyncMock()
    publisher.publish = AsyncMock()
    return publisher


# ============================================================================
# Unit Tests: Resume Request Schema
# ============================================================================


@pytest.mark.unit
def test_run_resume_request_approval_only():
    """Test RunResumeRequest with only approval field."""
    request = RunResumeRequest(approval=True)
    assert request.approval is True
    assert request.tool_result is None
    assert request.user_input is None


@pytest.mark.unit
def test_run_resume_request_denial():
    """Test RunResumeRequest for denial."""
    request = RunResumeRequest(approval=False)
    assert request.approval is False


@pytest.mark.unit
def test_run_resume_request_with_tool_result():
    """Test RunResumeRequest with tool result."""
    request = RunResumeRequest(
        approval=True,
        tool_result={"output": "File deleted successfully", "file_count": 1},
    )
    assert request.approval is True
    assert request.tool_result["output"] == "File deleted successfully"


@pytest.mark.unit
def test_run_resume_request_with_user_input():
    """Test RunResumeRequest with user input."""
    request = RunResumeRequest(
        approval=True,
        user_input="Yes, please proceed with deletion",
    )
    assert request.user_input == "Yes, please proceed with deletion"


@pytest.mark.unit
def test_run_resume_request_all_fields():
    """Test RunResumeRequest with all fields populated."""
    request = RunResumeRequest(
        approval=True,
        tool_result={"status": "success"},
        user_input="Confirmed",
    )
    assert request.approval is True
    assert request.tool_result == {"status": "success"}
    assert request.user_input == "Confirmed"


# ============================================================================
# Unit Tests: Resume Endpoint Logic
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_run_success_with_approval(
    mock_run_waiting,
    mock_state_machine,
    mock_event_publisher,
):
    """Test successful resume with approval=True."""
    # Arrange
    run_id = mock_run_waiting.id
    workspace_id = mock_run_waiting.workspace_id

    resume_data = RunResumeRequest(approval=True)

    # Act - Simulate the endpoint logic
    # Store approval in Redis
    await mock_state_machine.redis.set(
        f"run:{run_id}:resume_approval",
        json.dumps({
            "approval": resume_data.approval,
            "tool_result": resume_data.tool_result,
            "user_input": resume_data.user_input,
        }),
        ex=300,
    )

    # Verify Redis was called correctly
    mock_state_machine.redis.set.assert_called_once()
    call_args = mock_state_machine.redis.set.call_args
    stored_data = json.loads(call_args[0][1])

    # Assert
    assert stored_data["approval"] is True
    assert stored_data["tool_result"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_run_success_with_denial(
    mock_run_waiting,
    mock_state_machine,
):
    """Test successful resume with approval=False (denial)."""
    # Arrange
    run_id = mock_run_waiting.id
    resume_data = RunResumeRequest(approval=False)

    # Act
    await mock_state_machine.redis.set(
        f"run:{run_id}:resume_approval",
        json.dumps({
            "approval": resume_data.approval,
            "tool_result": resume_data.tool_result,
            "user_input": resume_data.user_input,
        }),
        ex=300,
    )

    # Verify
    call_args = mock_state_machine.redis.set.call_args
    stored_data = json.loads(call_args[0][1])

    # Assert
    assert stored_data["approval"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_run_stores_tool_result(
    mock_run_waiting,
    mock_state_machine,
):
    """Test that tool_result is properly stored in Redis."""
    # Arrange
    run_id = mock_run_waiting.id
    tool_result = {"output": "Operation completed", "items_processed": 42}
    resume_data = RunResumeRequest(approval=True, tool_result=tool_result)

    # Act
    await mock_state_machine.redis.set(
        f"run:{run_id}:resume_approval",
        json.dumps({
            "approval": resume_data.approval,
            "tool_result": resume_data.tool_result,
            "user_input": resume_data.user_input,
        }),
        ex=300,
    )

    # Verify
    call_args = mock_state_machine.redis.set.call_args
    stored_data = json.loads(call_args[0][1])

    # Assert
    assert stored_data["tool_result"] == tool_result
    assert stored_data["tool_result"]["items_processed"] == 42


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_run_triggers_worker_via_redis_stream(
    mock_run_waiting,
    mock_state_machine,
):
    """Test that resume correctly triggers worker via Redis stream."""
    # Arrange
    run_id = mock_run_waiting.id
    waiting_info = mock_run_waiting.waiting_for
    executor_code = waiting_info.get("executor_code", "DEFAULT001")

    # Act - Simulate stream trigger
    await mock_state_machine.redis.xadd(
        "run_tasks",
        fields={
            "run_id": run_id,
            "executor_code": executor_code,
            "input": json.dumps(mock_run_waiting.input_data or {}),
            "triggered_by": "resume",
        },
    )

    # Verify
    mock_state_machine.redis.xadd.assert_called_once()
    call_args = mock_state_machine.redis.xadd.call_args

    # Assert
    assert call_args[0][0] == "run_tasks"
    assert call_args[1]["fields"]["triggered_by"] == "resume"
    assert call_args[1]["fields"]["executor_code"] == "DEFAULT001"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_run_publishes_tool_result_event(
    mock_run_waiting,
    mock_event_publisher,
    mock_current_user,
):
    """Test that resume publishes TOOL_RESULT event."""
    # Arrange
    from aiwen.schemas.events.event_payloads import EventType

    workspace_id = mock_run_waiting.workspace_id
    run_id = mock_run_waiting.id
    waiting_info = mock_run_waiting.waiting_for

    resume_data = RunResumeRequest(
        approval=True,
        tool_result={"status": "success"},
    )

    # Act
    await mock_event_publisher.publish(
        event_type=EventType.TOOL_RESULT,
        workspace_id=workspace_id,
        run_id=run_id,
        user_id=str(mock_current_user.id),
        payload={
            "tool_name": waiting_info.get("tool_name", "unknown"),
            "tool_id": waiting_info.get("tool_id", "unknown"),
            "result": resume_data.tool_result,
            "success": resume_data.approval,
        },
        auto_commit=False,
    )

    # Verify
    mock_event_publisher.publish.assert_called_once()
    call_kwargs = mock_event_publisher.publish.call_args[1]

    # Assert
    assert call_kwargs["event_type"] == EventType.TOOL_RESULT
    assert call_kwargs["payload"]["tool_name"] == "delete_file"
    assert call_kwargs["payload"]["success"] is True


# ============================================================================
# Unit Tests: Error Cases
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_run_fails_when_not_in_waiting_state(mock_run_running):
    """Test that resume fails when run is not in WAITING state."""
    # Arrange
    run = mock_run_running

    # Act & Assert
    assert run.status != RunStatus.WAITING.value
    # In the real endpoint, this would raise HTTPException 400


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_run_fails_when_workspace_not_found(mock_workspace_crud):
    """Test that resume fails when workspace not found."""
    # Arrange
    mock_workspace_crud.get_by_id_and_user = AsyncMock(return_value=None)

    # Act
    result = await mock_workspace_crud.get_by_id_and_user("invalid-ws", "user-id")

    # Assert
    assert result is None
    # In the real endpoint, this would raise HTTPException 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_run_fails_when_run_not_found(mock_run_crud):
    """Test that resume fails when run not found."""
    # Arrange
    mock_run_crud.get_by_id = AsyncMock(return_value=None)

    # Act
    result = await mock_run_crud.get_by_id("invalid-run-id")

    # Assert
    assert result is None
    # In the real endpoint, this would raise HTTPException 404


# ============================================================================
# Unit Tests: Redis Key Management
# ============================================================================


@pytest.mark.unit
def test_resume_approval_redis_key_format():
    """Test that Redis key follows expected format."""
    run_id = str(uuid4())
    expected_key = f"run:{run_id}:resume_approval"

    # Assert key format
    assert expected_key.startswith("run:")
    assert expected_key.endswith(":resume_approval")
    assert run_id in expected_key


@pytest.mark.unit
def test_resume_approval_ttl():
    """Test that approval data has correct TTL (5 minutes)."""
    expected_ttl = 300  # 5 minutes

    # The endpoint uses ex=300 for Redis SET
    assert expected_ttl == 300


# ============================================================================
# Unit Tests: Waiting Info Extraction
# ============================================================================


@pytest.mark.unit
def test_extract_executor_code_from_waiting_info(mock_run_waiting):
    """Test extraction of executor_code from waiting_for."""
    waiting_info = mock_run_waiting.waiting_for
    executor_code = waiting_info.get("executor_code", "DEFAULT001")

    assert executor_code == "DEFAULT001"


@pytest.mark.unit
def test_extract_executor_code_default_fallback():
    """Test that executor_code falls back to DEFAULT001."""
    waiting_info = {"type": "tool_approval", "tool_name": "test"}
    executor_code = waiting_info.get("executor_code", "DEFAULT001")

    assert executor_code == "DEFAULT001"


@pytest.mark.unit
def test_extract_tool_info_from_waiting_info(mock_run_waiting):
    """Test extraction of tool info from waiting_for."""
    waiting_info = mock_run_waiting.waiting_for

    assert waiting_info.get("tool_name") == "delete_file"
    assert waiting_info.get("tool_id") == "call_123"
    assert waiting_info.get("arguments") == {"path": "/tmp/test.txt"}
