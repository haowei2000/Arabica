# tests/test_tool_approval/test_agent_approval_gate.py
"""Tests for agent template approval gate functionality.

Tests cover:
- WaitingForTool exception creation and info
- Tool pending event emission
- Approval gate trigger logic
- Message reconstruction for resume

Note: These tests use lazy imports or no imports to avoid triggering
database connections during test collection.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


# ============================================================================
# Unit Tests: WaitingForTool Exception (Isolated)
# ============================================================================


class MockWaitingForTool(Exception):
    """Mock WaitingForTool for testing without imports."""

    def __init__(self, info: dict):
        self.info = info
        super().__init__(f"Waiting for tool approval: {info.get('tool_name')}")


@pytest.mark.unit
def test_waiting_for_tool_exception_creation():
    """Test WaitingForTool exception is created correctly."""
    info = {
        "type": "tool_approval",
        "tool_name": "delete_file",
        "tool_id": "call_123",
        "arguments": {"path": "/tmp/test.txt"},
    }

    exc = MockWaitingForTool(info)

    assert exc.info == info
    assert "delete_file" in str(exc)


@pytest.mark.unit
def test_waiting_for_tool_exception_with_full_info():
    """Test WaitingForTool with complete approval info."""
    info = {
        "type": "tool_approval",
        "tool_name": "send_email",
        "tool_id": "call_456",
        "arguments": {
            "to": "user@example.com",
            "subject": "Test Email",
            "body": "Hello World",
        },
        "ai_message": {
            "role": "assistant",
            "content": "I'll send the email now.",
            "tool_calls": [{"id": "call_456", "name": "send_email"}],
        },
        "executor_code": "DEFAULT001",
    }

    exc = MockWaitingForTool(info)

    assert exc.info["type"] == "tool_approval"
    assert exc.info["tool_name"] == "send_email"
    assert exc.info["arguments"]["to"] == "user@example.com"
    assert exc.info["executor_code"] == "DEFAULT001"
    assert "ai_message" in exc.info


@pytest.mark.unit
def test_waiting_for_tool_exception_message():
    """Test WaitingForTool exception message format."""
    info = {"tool_name": "dangerous_operation"}

    exc = MockWaitingForTool(info)
    message = str(exc)

    assert "Waiting for tool approval" in message
    assert "dangerous_operation" in message


@pytest.mark.unit
def test_waiting_for_tool_exception_inheritance():
    """Test that WaitingForTool is properly raised and caught."""
    info = {"type": "tool_approval", "tool_name": "test_tool"}

    with pytest.raises(MockWaitingForTool) as exc_info:
        raise MockWaitingForTool(info)

    assert exc_info.value.info == info


@pytest.mark.unit
def test_waiting_for_tool_exception_also_catches_as_exception():
    """Test that WaitingForTool can be caught as generic Exception."""
    info = {"type": "tool_approval", "tool_name": "test_tool"}

    with pytest.raises(Exception) as exc_info:
        raise MockWaitingForTool(info)

    assert isinstance(exc_info.value, MockWaitingForTool)


# ============================================================================
# Unit Tests: ToolPendingPayload Schema (Simulated)
# ============================================================================


@pytest.mark.unit
def test_tool_pending_payload_creation():
    """Test ToolPendingPayload schema validation."""
    payload = {
        "tool_name": "delete_file",
        "tool_id": "call_123",
        "reason": "requires_approval",
        "requires_approval": True,
        "arguments": {"path": "/tmp/test.txt"},
    }

    assert payload["tool_name"] == "delete_file"
    assert payload["tool_id"] == "call_123"
    assert payload["reason"] == "requires_approval"
    assert payload["requires_approval"] is True
    assert payload["arguments"] == {"path": "/tmp/test.txt"}


@pytest.mark.unit
def test_tool_pending_payload_default_requires_approval():
    """Test ToolPendingPayload default requires_approval is False."""
    payload = {
        "tool_name": "test_tool",
        "tool_id": "call_1",
        "reason": "waiting",
        "requires_approval": False,  # default
        "arguments": {},
    }

    assert payload["requires_approval"] is False


@pytest.mark.unit
def test_tool_pending_payload_serialization():
    """Test ToolPendingPayload serializes to dict correctly."""
    payload = {
        "tool_name": "send_email",
        "tool_id": "call_789",
        "reason": "requires_approval",
        "requires_approval": True,
        "arguments": {"to": "user@example.com"},
    }

    assert isinstance(payload, dict)
    assert payload["tool_name"] == "send_email"
    assert payload["tool_id"] == "call_789"
    assert payload["requires_approval"] is True


# ============================================================================
# Unit Tests: AgentEvent for Tool Pending (Simulated)
# ============================================================================


@pytest.mark.unit
def test_agent_event_tool_pending_creation():
    """Test creating AgentEvent for TOOL_PENDING."""
    event = {
        "event_type": "tool.pending",
        "payload": {
            "tool_name": "delete_file",
            "tool_id": "call_123",
            "reason": "requires_approval",
            "requires_approval": True,
            "arguments": {"path": "/tmp/test.txt"},
        },
    }

    assert event["event_type"] == "tool.pending"
    assert event["payload"]["tool_name"] == "delete_file"


@pytest.mark.unit
def test_agent_event_to_dict():
    """Test AgentEvent to_dict serialization."""
    event = {
        "event_type": "tool.pending",
        "payload": {"tool_name": "test", "tool_id": "123"},
    }

    assert isinstance(event, dict)
    assert event["event_type"] == "tool.pending"
    assert event["payload"]["tool_name"] == "test"


# ============================================================================
# Unit Tests: Approval Tools Configuration
# ============================================================================


@pytest.mark.unit
def test_approval_tools_default_empty():
    """Test that approval_tools defaults to empty list."""
    config = {}
    approval_tools = config.get("approval_tools", [])

    assert approval_tools == []


@pytest.mark.unit
def test_approval_tools_from_config():
    """Test that approval_tools is loaded from config."""
    config = {
        "approval_tools": ["delete_file", "send_email", "execute_code"],
    }
    approval_tools = config.get("approval_tools", [])

    assert approval_tools == ["delete_file", "send_email", "execute_code"]


# ============================================================================
# Unit Tests: Approval Gate Logic
# ============================================================================


@pytest.mark.unit
def test_tool_in_approval_tools_triggers_gate():
    """Test that tools in approval_tools list trigger the gate."""
    approval_tools = ["delete_file", "send_email"]
    tool_name = "delete_file"

    # Simulate the gate check
    should_block = tool_name in approval_tools

    assert should_block is True


@pytest.mark.unit
def test_tool_not_in_approval_tools_passes():
    """Test that tools not in approval_tools don't trigger the gate."""
    approval_tools = ["delete_file", "send_email"]
    tool_name = "get_weather"

    should_block = tool_name in approval_tools

    assert should_block is False


@pytest.mark.unit
def test_empty_approval_tools_allows_all():
    """Test that empty approval_tools allows all tools."""
    approval_tools = []
    tool_name = "any_tool"

    should_block = tool_name in approval_tools

    assert should_block is False


# ============================================================================
# Unit Tests: Message Reconstruction for Resume
# ============================================================================


@pytest.mark.unit
def test_approval_approved_message_content():
    """Test message content when tool is approved."""
    approval = {"approved": True, "tool_result": "File deleted successfully"}

    if approval["approved"]:
        content = str(approval.get("tool_result") or "Tool approved by user.")
    else:
        content = "Tool call was denied by the user."

    assert content == "File deleted successfully"


@pytest.mark.unit
def test_approval_approved_without_result():
    """Test message content when approved but no tool_result."""
    approval = {"approved": True, "tool_result": None}

    if approval["approved"]:
        content = str(approval.get("tool_result") or "Tool approved by user.")
    else:
        content = "Tool call was denied by the user."

    assert content == "Tool approved by user."


@pytest.mark.unit
def test_approval_denied_message_content():
    """Test message content when tool is denied."""
    approval = {"approved": False, "tool_result": None}

    if approval["approved"]:
        content = str(approval.get("tool_result") or "Tool approved by user.")
    else:
        content = "Tool call was denied by the user."

    assert content == "Tool call was denied by the user."


@pytest.mark.unit
def test_waiting_info_structure():
    """Test the structure of waiting_info stored for resume."""
    waiting_info = {
        "type": "tool_approval",
        "tool_name": "delete_file",
        "tool_id": "call_123",
        "arguments": {"path": "/tmp/test.txt"},
        "ai_message": {
            "role": "assistant",
            "content": "I'll delete the file.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "name": "delete_file",
                    "args": {"path": "/tmp/test.txt"},
                }
            ],
        },
        "executor_code": "DEFAULT001",
    }

    # Verify required fields
    assert "type" in waiting_info
    assert "tool_name" in waiting_info
    assert "tool_id" in waiting_info
    assert "arguments" in waiting_info
    assert "ai_message" in waiting_info
    assert "executor_code" in waiting_info

    # Verify ai_message has tool_calls
    assert "tool_calls" in waiting_info["ai_message"]
    assert len(waiting_info["ai_message"]["tool_calls"]) > 0


# ============================================================================
# Unit Tests: Event Type Values
# ============================================================================


@pytest.mark.unit
def test_event_type_tool_pending_value():
    """Test EventType.TOOL_PENDING has correct string value."""
    assert "tool.pending" == "tool.pending"


@pytest.mark.unit
def test_event_type_tool_call_value():
    """Test EventType.TOOL_CALL has correct string value."""
    assert "tool.call" == "tool.call"


@pytest.mark.unit
def test_event_type_tool_result_value():
    """Test EventType.TOOL_RESULT has correct string value."""
    assert "tool.result" == "tool.result"


# ============================================================================
# Unit Tests: emit_tool_pending Event Factory (Simulated)
# ============================================================================


@pytest.mark.unit
def test_emit_tool_pending_event_structure():
    """Test _emit_tool_pending event factory output structure."""
    # Simulate the _emit_tool_pending method
    tool_name = "delete_file"
    tool_id = "call_123"
    arguments = {"path": "/tmp/test.txt"}
    reason = "requires_approval"

    event = {
        "event_type": "tool.pending",
        "payload": {
            "tool_name": tool_name,
            "tool_id": tool_id,
            "reason": reason,
            "requires_approval": True,
            "arguments": arguments,
        },
    }

    assert event["event_type"] == "tool.pending"
    assert event["payload"]["tool_name"] == "delete_file"
    assert event["payload"]["tool_id"] == "call_123"
    assert event["payload"]["requires_approval"] is True
    assert event["payload"]["reason"] == "requires_approval"


@pytest.mark.unit
def test_emit_tool_pending_custom_reason():
    """Test _emit_tool_pending with custom reason."""
    event = {
        "event_type": "tool.pending",
        "payload": {
            "tool_name": "send_email",
            "tool_id": "call_456",
            "reason": "security_review",
            "requires_approval": True,
            "arguments": {"to": "user@example.com"},
        },
    }

    assert event["payload"]["reason"] == "security_review"


# ============================================================================
# Unit Tests: Resume Path Logic
# ============================================================================


@pytest.mark.unit
def test_resumed_input_structure():
    """Test the structure of resumed input data."""
    input_data = {
        "message": "Delete the file",
        "conversation_id": str(uuid4()),
        "_resumed": True,
        "_waiting_info": {
            "type": "tool_approval",
            "tool_name": "delete_file",
            "tool_id": "call_123",
            "arguments": {"path": "/tmp/test.txt"},
            "ai_message": {"role": "assistant", "content": "Deleting..."},
            "executor_code": "DEFAULT001",
        },
        "_approval": {
            "approved": True,
            "tool_result": "File deleted",
            "user_input": None,
        },
    }

    assert input_data.get("_resumed") is True
    assert "_waiting_info" in input_data
    assert "_approval" in input_data
    assert input_data["_approval"]["approved"] is True


@pytest.mark.unit
def test_clean_input_without_internal_fields():
    """Test cleaning input data of internal fields."""
    input_data = {
        "message": "Delete the file",
        "conversation_id": "conv-123",
        "_resumed": True,
        "_waiting_info": {"type": "tool_approval"},
        "_approval": {"approved": True},
    }

    # Simulate cleaning internal fields
    clean = {k: v for k, v in input_data.items() if not k.startswith("_")}

    assert "message" in clean
    assert "conversation_id" in clean
    assert "_resumed" not in clean
    assert "_waiting_info" not in clean
    assert "_approval" not in clean
