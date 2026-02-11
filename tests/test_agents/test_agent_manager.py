# tests/test_agents/test_agent_manager.py
from unittest.mock import AsyncMock

from aiwen.services.executor.manager import AgentManager
import pytest

from aiwen.services.executor.executor_factory import AppFactory


@pytest.fixture
def mock_db_session():
    """Fixture to create a mock database session."""
    return AsyncMock()


@pytest.fixture
def agent_manager(mock_db_session):
    """Fixture to create an AgentManager instance with mock session."""
    return AgentManager(mock_db_session)


def test_agent_manager_initialization(mock_db_session):
    """Test that AgentManager initializes correctly."""
    manager = AgentManager(mock_db_session)
    assert manager.crud is not None
    assert manager.factory == AppFactory


@pytest.mark.asyncio
async def test_get_available_agent_types(agent_manager):
    """Test getting available agent types."""
    agent_types = agent_manager.get_available_agent_types()
    assert isinstance(agent_types, list)
    assert "NL2SQLAgent" in agent_types
    assert "AnomalyAgent" in agent_types


@pytest.mark.asyncio
async def test_is_agent_type_registered(agent_manager):
    """Test checking if agent type is registered."""
    # Test registered agent type
    assert AppFactory.is_agent_type_registered("NL2SQLAgent") is True

    # Test unregistered agent type
    assert AppFactory.is_agent_type_registered("UnknownAgent") is False


@pytest.mark.asyncio
async def test_register_custom_agent_type(agent_manager):
    """Test registering a custom agent type."""

    class CustomAgent:
        pass

    # Register custom agent
    await agent_manager.register_custom_agent_type("CustomAgent", CustomAgent)

    # Verify it's registered
    assert AppFactory.is_agent_type_registered("CustomAgent") is True
