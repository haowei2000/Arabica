# tests/test_agents/test_agent_factory.py
import pytest
from aiwen.services.agent.concrete import AnomalyAgent, NL2SQLAgent

from aiwen.services.agent.app_factory import AppFactory


def test_agent_factory_registration():
    """Test that agent are properly registered in the factory."""
    available_agents = AppFactory.get_available_agents()
    assert "NL2SQLAgent" in available_agents
    assert "AnomalyAgent" in available_agents


def test_agent_factory_creation():
    """Test that agent can be created through the factory."""
    # Test NL2SQLAgent creation
    nl2sql_agent = AppFactory.create("NL2SQLAgent", {})
    assert isinstance(nl2sql_agent, NL2SQLAgent)

    # Test AnomalyAgent creation
    anomaly_agent = AppFactory.create("AnomalyAgent", {})
    assert isinstance(anomaly_agent, AnomalyAgent)


def test_agent_factory_unknown_agent():
    """Test that unknown agent types raise ValueError."""
    with pytest.raises(ValueError, match="Unknown agent_type: UnknownAgent"):
        AppFactory.create("UnknownAgent", {})
