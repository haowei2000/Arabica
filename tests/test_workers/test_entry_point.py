# tests/test_workers/test_entry_point.py
import sys
from unittest.mock import AsyncMock, patch

from aiwen.workers.cli import WorkerCLI, main
import pytest


def test_worker_cli_initialization():
    """Test that WorkerCLI can be initialized."""
    cli = WorkerCLI()
    assert cli.worker is None
    assert cli.running is False


@pytest.mark.asyncio
async def test_worker_cli_start_failure():
    """Test that WorkerCLI handles startup failures gracefully."""
    cli = WorkerCLI()

    # Mock the database initialization to fail
    with patch(
        "aiwen.workers.cli._ensure_registered", side_effect=Exception("DB Error")
    ):
        result = await cli.start()
        assert result is False


@pytest.mark.asyncio
async def test_main_function():
    """Test the main function."""
    # Mock the CLI to avoid actual startup
    with patch("aiwen.workers.cli.WorkerCLI") as mock_cli_class:
        mock_cli = AsyncMock()
        mock_cli.start.return_value = True
        mock_cli_class.return_value = mock_cli

        with patch("sys.exit") as mock_exit:
            await main()
            mock_exit.assert_called_once_with(0)
