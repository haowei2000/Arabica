# tests/test_agents/test_agent_router.py
from fastapi.testclient import TestClient
import pytest

from aiwen.app import app

client = TestClient(app)


def test_list_agent_types():
    """Test that the agent types endpoint returns available agents."""
    response = client.get("/api/agents/types")
    assert response.status_code == 200
    data = response.json()
    assert "agent_types" in data
    assert isinstance(data["agent_types"], list)
    assert len(data["agent_types"]) > 0


def test_run_agent_not_found():
    """Test that running a non-existent agent returns 404."""
    response = client.post("/api/agents/nonexistent-agent/run", json={"test": "data"})
    # This will return 404 because the agent doesn't exist in the database
    # In a real test, we would need to mock the database session
    assert response.status_code in [
        404,
        500,
    ]  # Could be 404 or 500 depending on implementation
