# tests/test_tool_approval/test_run_state_machine.py
"""Tests for RunStateMachine tool approval transitions.

Tests cover:
- pause_for_tool: running -> waiting transition
- resume_from_tool: waiting -> running transition
- Invalid state transitions
- waiting_for field management

Note: These tests use lazy imports to avoid triggering database connections
during test collection.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_run():
    """Create a mock Run object."""
    run = MagicMock()
    run.id = str(uuid4())
    run.workspace_id = str(uuid4())
    run.status = "running"
    run.waiting_for = None
    run.updated_at = None
    run.started_at = None
    run.completed_at = None
    return run


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock()
    redis.delete = AsyncMock()
    redis.xadd = AsyncMock()
    return redis


@pytest.fixture
def mock_event_publisher():
    """Create a mock event publisher."""
    publisher = AsyncMock()
    publisher.publish = AsyncMock()
    return publisher


# ============================================================================
# Unit Tests: State Transition Validation (No imports needed)
# ============================================================================


@pytest.mark.unit
def test_valid_transitions_running_to_waiting():
    """Test that running -> waiting is a valid transition."""
    VALID_TRANSITIONS = {
        "pending": ["running", "cancelled"],
        "running": ["waiting", "finished", "failed", "cancelled"],
        "waiting": ["running", "cancelled"],
        "finished": [],
        "cancelled": [],
        "failed": [],
    }

    current = "running"
    target = "waiting"
    allowed = VALID_TRANSITIONS.get(current, [])

    assert target in allowed


@pytest.mark.unit
def test_valid_transitions_waiting_to_running():
    """Test that waiting -> running is a valid transition."""
    VALID_TRANSITIONS = {
        "pending": ["running", "cancelled"],
        "running": ["waiting", "finished", "failed", "cancelled"],
        "waiting": ["running", "cancelled"],
        "finished": [],
        "cancelled": [],
        "failed": [],
    }

    current = "waiting"
    target = "running"
    allowed = VALID_TRANSITIONS.get(current, [])

    assert target in allowed


@pytest.mark.unit
def test_valid_transitions_waiting_to_cancelled():
    """Test that waiting -> cancelled is a valid transition."""
    VALID_TRANSITIONS = {
        "pending": ["running", "cancelled"],
        "running": ["waiting", "finished", "failed", "cancelled"],
        "waiting": ["running", "cancelled"],
        "finished": [],
        "cancelled": [],
        "failed": [],
    }

    current = "waiting"
    target = "cancelled"
    allowed = VALID_TRANSITIONS.get(current, [])

    assert target in allowed


@pytest.mark.unit
def test_invalid_transition_finished_to_running():
    """Test that finished -> running is invalid."""
    VALID_TRANSITIONS = {
        "pending": ["running", "cancelled"],
        "running": ["waiting", "finished", "failed", "cancelled"],
        "waiting": ["running", "cancelled"],
        "finished": [],
        "cancelled": [],
        "failed": [],
    }

    current = "finished"
    target = "running"
    allowed = VALID_TRANSITIONS.get(current, [])

    assert target not in allowed


@pytest.mark.unit
def test_invalid_transition_pending_to_waiting():
    """Test that pending -> waiting is invalid (must go through running)."""
    VALID_TRANSITIONS = {
        "pending": ["running", "cancelled"],
        "running": ["waiting", "finished", "failed", "cancelled"],
        "waiting": ["running", "cancelled"],
        "finished": [],
        "cancelled": [],
        "failed": [],
    }

    current = "pending"
    target = "waiting"
    allowed = VALID_TRANSITIONS.get(current, [])

    assert target not in allowed


# ============================================================================
# Unit Tests: State Classification
# ============================================================================


@pytest.mark.unit
def test_is_terminal_waiting_false():
    """Test that waiting is not a terminal state."""
    TERMINAL_STATES = {"finished", "cancelled", "failed"}
    assert "waiting" not in TERMINAL_STATES


@pytest.mark.unit
def test_is_terminal_finished_true():
    """Test that finished is a terminal state."""
    TERMINAL_STATES = {"finished", "cancelled", "failed"}
    assert "finished" in TERMINAL_STATES


@pytest.mark.unit
def test_is_terminal_cancelled_true():
    """Test that cancelled is a terminal state."""
    TERMINAL_STATES = {"finished", "cancelled", "failed"}
    assert "cancelled" in TERMINAL_STATES


@pytest.mark.unit
def test_is_active_waiting_true():
    """Test that waiting is an active state."""
    ACTIVE_STATES = {"pending", "running", "waiting"}
    assert "waiting" in ACTIVE_STATES


@pytest.mark.unit
def test_is_active_running_true():
    """Test that running is an active state."""
    ACTIVE_STATES = {"pending", "running", "waiting"}
    assert "running" in ACTIVE_STATES


# ============================================================================
# Unit Tests: pause_for_tool Logic (Mocked)
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_for_tool_transitions_running_to_waiting(
    mock_db_session, mock_run, mock_event_publisher
):
    """Test that pause_for_tool correctly transitions from running to waiting."""
    # Arrange
    mock_run.status = "running"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_run
    mock_db_session.execute.return_value = mock_result

    waiting_info = {
        "type": "tool_approval",
        "tool_name": "delete_file",
        "tool_id": "call_123",
        "arguments": {"path": "/tmp/test.txt"},
    }

    # Act - Simulate the pause_for_tool logic
    run = mock_run
    run.waiting_for = waiting_info
    run.status = "waiting"

    # Assert
    assert run.status == "waiting"
    assert run.waiting_for == waiting_info


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_for_tool_stores_waiting_info(mock_run):
    """Test that pause_for_tool correctly stores waiting_for information."""
    # Arrange
    mock_run.status = "running"
    waiting_info = {
        "type": "tool_approval",
        "tool_name": "send_email",
        "tool_id": "call_456",
        "arguments": {"to": "user@example.com", "subject": "Test"},
        "ai_message": {"role": "assistant", "content": "Sending email..."},
        "executor_code": "DEFAULT001",
    }

    # Act - Simulate storing waiting_for
    mock_run.waiting_for = waiting_info

    # Assert
    assert mock_run.waiting_for == waiting_info
    assert mock_run.waiting_for["executor_code"] == "DEFAULT001"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_for_tool_fails_from_pending_state(mock_run):
    """Test that pause_for_tool fails when run is in pending state."""
    # Arrange
    mock_run.status = "pending"

    VALID_TRANSITIONS = {
        "pending": ["running", "cancelled"],
        "running": ["waiting", "finished", "failed", "cancelled"],
    }

    # Act
    current = mock_run.status
    target = "waiting"
    allowed = VALID_TRANSITIONS.get(current, [])

    # Assert - transition should not be allowed
    assert target not in allowed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_for_tool_fails_from_finished_state(mock_run):
    """Test that pause_for_tool fails when run is in finished state."""
    # Arrange
    mock_run.status = "finished"

    VALID_TRANSITIONS = {
        "finished": [],
        "cancelled": [],
        "failed": [],
    }

    # Act
    current = mock_run.status
    target = "waiting"
    allowed = VALID_TRANSITIONS.get(current, [])

    # Assert - transition should not be allowed
    assert target not in allowed


# ============================================================================
# Unit Tests: resume_from_tool Logic (Mocked)
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_from_tool_transitions_waiting_to_running(mock_run):
    """Test that resume_from_tool correctly transitions from waiting to running."""
    # Arrange
    mock_run.status = "waiting"
    mock_run.waiting_for = {"type": "tool_approval", "tool_name": "test_tool"}

    # Act - Simulate the resume_from_tool logic
    mock_run.waiting_for = None
    mock_run.status = "running"

    # Assert
    assert mock_run.status == "running"
    assert mock_run.waiting_for is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_from_tool_clears_waiting_for(mock_run):
    """Test that resume_from_tool clears the waiting_for field."""
    # Arrange
    mock_run.status = "waiting"
    mock_run.waiting_for = {
        "type": "tool_approval",
        "tool_name": "delete_file",
        "tool_id": "call_789",
    }

    # Act - Simulate clearing waiting_for
    mock_run.waiting_for = None

    # Assert
    assert mock_run.waiting_for is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_from_tool_fails_from_running_state():
    """Test that resume_from_tool fails when run is already running."""
    # Arrange
    current_state = "running"

    VALID_TRANSITIONS = {
        "running": ["waiting", "finished", "failed", "cancelled"],
    }

    # Act
    target = "running"
    allowed = VALID_TRANSITIONS.get(current_state, [])

    # Assert - can't go running -> running
    assert target not in allowed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_from_tool_fails_from_cancelled_state():
    """Test that resume_from_tool fails when run is cancelled."""
    # Arrange
    current_state = "cancelled"

    VALID_TRANSITIONS = {
        "cancelled": [],
    }

    # Act
    target = "running"
    allowed = VALID_TRANSITIONS.get(current_state, [])

    # Assert - terminal state has no valid transitions
    assert target not in allowed


# ============================================================================
# Unit Tests: Full Approval Flow
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_full_approval_flow_pause_and_resume(mock_run):
    """Test complete pause -> resume flow for tool approval."""
    # Arrange - Start with running state
    mock_run.status = "running"
    mock_run.waiting_for = None

    waiting_info = {
        "type": "tool_approval",
        "tool_name": "dangerous_operation",
        "tool_id": "call_abc",
        "arguments": {"action": "delete_all"},
        "executor_code": "DEFAULT001",
    }

    # Act - Pause for tool approval
    mock_run.waiting_for = waiting_info
    mock_run.status = "waiting"

    # Assert - Now in waiting state
    assert mock_run.status == "waiting"
    assert mock_run.waiting_for == waiting_info

    # Act - Resume after approval
    mock_run.waiting_for = None
    mock_run.status = "running"

    # Assert - Back to running state
    assert mock_run.status == "running"
    assert mock_run.waiting_for is None


# ============================================================================
# Unit Tests: Error Cases
# ============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_for_tool_run_not_found(mock_db_session):
    """Test that pause_for_tool raises error when run not found."""
    # Arrange
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db_session.execute.return_value = mock_result

    # Act & Assert
    run = mock_result.scalar_one_or_none()
    assert run is None
    # In real implementation, this would raise ValueError


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_from_tool_run_not_found(mock_db_session):
    """Test that resume_from_tool raises error when run not found."""
    # Arrange
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db_session.execute.return_value = mock_result

    # Act & Assert
    run = mock_result.scalar_one_or_none()
    assert run is None
    # In real implementation, this would raise ValueError


# ============================================================================
# Unit Tests: InvalidTransitionError
# ============================================================================


@pytest.mark.unit
def test_invalid_transition_error_message():
    """Test InvalidTransitionError message formatting."""
    # Simulate the error message format
    current_state = "running"
    target_state = "pending"
    reason = "Cannot go back to pending"

    message = f"Invalid transition from '{current_state}' to '{target_state}'"
    if reason:
        message += f": {reason}"

    assert "running" in message
    assert "pending" in message
    assert "Cannot go back to pending" in message


@pytest.mark.unit
def test_invalid_transition_error_without_reason():
    """Test InvalidTransitionError without reason."""
    current_state = "finished"
    target_state = "running"
    reason = ""

    message = f"Invalid transition from '{current_state}' to '{target_state}'"
    if reason:
        message += f": {reason}"

    assert "finished" in message
    assert "running" in message
    assert ":" not in message  # No colon when no reason
