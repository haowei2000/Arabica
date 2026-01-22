# tests/test_agents/test_agent_crud.py
from datetime import datetime
from uuid import uuid4

import pytest

from aiwen.models.agents.app import App


@pytest.mark.asyncio
async def test_create_agent():
    """Test creating an agent."""
    # This would normally use a test database session
    # For now, we'll just test the model creation
    agent = App(
        id=uuid4(),
        app_code="test-agent-1",
        agent_template_id=None,  # New field instead of agent_type
        enabled=True,
        config={
            "model": "gpt-4.1",
            "agent_type": "NL2SQLAgent",
        },  # Store agent_type in config
        version=1,
        created_at=datetime.utcnow(),
    )

    assert agent.app_code == "test-agent-1"
    assert agent.agent_template_id is None
    assert agent.enabled is True
    assert agent.config == {"model": "gpt-4.1", "agent_type": "NL2SQLAgent"}
    assert agent.version == 1


@pytest.mark.asyncio
async def test_agent_repr():
    """Test agent string representation."""
    agent_id = uuid4()
    agent = App(
        id=agent_id,
        app_code="test-agent-1",
        agent_template_id=None,  # New field instead of agent_type
        enabled=True,
    )

    expected = f"<App(id={agent_id}, app_code='test-agent-1', agent_template_id='None', enabled=True)>"
    assert repr(agent) == expected
