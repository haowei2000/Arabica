# tests/test_agents/test_agent_model.py
from datetime import datetime
from uuid import uuid4

import pytest

from aiwen.models.app import App


def test_agent_model_creation():
    """Test that Agent model can be instantiated correctly."""
    agent_code = "test-agent-code"
    agent = App(
        id=uuid4(),
        agent_code=agent_code,
        agent_type="NL2SQLAgent",
        enabled=True,
        config={"model": "gpt-4.1"},
        version=1,
        created_at=datetime.utcnow(),
    )

    assert agent.app_code == agent_code
    assert agent.app_type == "NL2SQLAgent"
    assert agent.enabled is True
    assert agent.config == {"model": "gpt-4.1"}
    assert agent.version == 1


def test_agent_model_defaults():
    """Test that Agent model defaults are applied correctly."""
    agent = App(id=uuid4(), agent_code="test-agent", agent_type="TestAgent")

    assert agent.enabled is True
    assert agent.version == 1
    assert agent.config is None
    assert agent.created_at is not None
