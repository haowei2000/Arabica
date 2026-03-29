# tests/test_workers/test_agent_worker.py
from unittest.mock import AsyncMock, Mock

from structure.celery_worker.task_worker import AgentWorker
import pytest


def test_agent_worker_initialization():
    """Test that AgentWorker can be initialized."""
    # Create mock redis client and db_session session factory
    mock_redis = AsyncMock()
    mock_db_factory = Mock()

    # Create worker instance
    worker = AgentWorker(mock_redis, mock_db_factory)

    # Verify attributes are set correctly
    assert worker.redis_client == mock_redis
    assert worker.db_session == mock_db_factory
    assert worker.pubsub is None


@pytest.mark.asyncio
async def test_agent_worker_publish_event():
    """Test that AgentWorker can publish events."""
    # Create mock redis client and db_session session factory
    mock_redis = AsyncMock()
    mock_db_factory = Mock()

    # Create worker instance
    worker = AgentWorker(mock_redis, mock_db_factory)

    # Test publish_event method
    test_task_id = "123e4567-e89b-12d3-a456-426614174000"
    test_event_data = {"event": "test", "data": "test input"}

    await worker.publish_event(test_task_id, test_event_data)

    # Verify that redis client publish was called
    mock_redis.publish.assert_called_once_with(
        f"agent:task:{test_task_id}", '{"event": "test", "data": "test input"}'
    )
