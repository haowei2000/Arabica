"""
Tests for the Tool Execution Mode system.

Tests cover:
- Execution mode enum and metadata
- Decorators (@sandbox_tool, @client_tool, @server_tool)
- ExecutionRouter routing logic
- SandboxExecutor (mocked Docker)
- ClientExecutor (mocked Redis)
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from aiwen.schemas.tools.execution import (
    ExecutionContext,
    ToolResult,
    ToolResultSubmission,
)
from aiwen.services.agent.tools.execution_mode import (
    ResourceLimits,
    ToolExecutionMode,
    ToolMetadata,
    _tool_metadata_registry,
    client_tool,
    get_tool_metadata,
    list_tools_by_mode,
    register_tool_metadata,
    sandbox_tool,
    server_tool,
)
from aiwen.services.agent.tools.execution_router import ExecutionRouter


class TestToolExecutionMode:
    """Tests for ToolExecutionMode enum."""

    def test_enum_values(self):
        """Test that all execution modes have correct string values."""
        assert ToolExecutionMode.SERVER.value == "server"
        assert ToolExecutionMode.SANDBOX.value == "sandbox"
        assert ToolExecutionMode.CLIENT.value == "client"

    def test_enum_is_string(self):
        """Test that enum values can be used as strings."""
        assert str(ToolExecutionMode.SERVER) == "ToolExecutionMode.SERVER"
        assert ToolExecutionMode.SERVER == "server"


class TestResourceLimits:
    """Tests for ResourceLimits dataclass."""

    def test_default_values(self):
        """Test default resource limits."""
        limits = ResourceLimits()

        assert limits.memory == "256m"
        assert limits.cpu_period == 100000
        assert limits.cpu_quota == 50000
        assert limits.network_disabled is True
        assert limits.read_only_rootfs is True
        assert limits.pids_limit == 100

    def test_custom_values(self):
        """Test custom resource limits."""
        limits = ResourceLimits(
            memory="1g",
            cpu_quota=100000,
            network_disabled=False,
        )

        assert limits.memory == "1g"
        assert limits.cpu_quota == 100000
        assert limits.network_disabled is False


class TestToolMetadata:
    """Tests for ToolMetadata dataclass."""

    def test_default_values(self):
        """Test default metadata values."""
        metadata = ToolMetadata()

        assert metadata.execution_mode == ToolExecutionMode.SERVER
        assert metadata.timeout_seconds == 30
        assert metadata.sandbox_image is None
        assert metadata.client_handler is None

    def test_sandbox_metadata(self):
        """Test sandbox-specific metadata."""
        metadata = ToolMetadata(
            execution_mode=ToolExecutionMode.SANDBOX,
            sandbox_image="python:3.12-slim",
            timeout_seconds=60,
        )

        assert metadata.execution_mode == ToolExecutionMode.SANDBOX
        assert metadata.sandbox_image == "python:3.12-slim"

    def test_client_metadata(self):
        """Test client-specific metadata."""
        metadata = ToolMetadata(
            execution_mode=ToolExecutionMode.CLIENT,
            client_handler="filePicker",
            timeout_seconds=120,
            client_config={"multiple": True},
        )

        assert metadata.execution_mode == ToolExecutionMode.CLIENT
        assert metadata.client_handler == "filePicker"
        assert metadata.client_config == {"multiple": True}


class TestToolMetadataRegistry:
    """Tests for tool metadata registration."""

    def test_register_and_get_metadata(self):
        """Test registering and retrieving metadata."""
        metadata = ToolMetadata(
            execution_mode=ToolExecutionMode.SANDBOX,
            sandbox_image="test:latest",
        )

        register_tool_metadata("test_tool", metadata)
        retrieved = get_tool_metadata("test_tool")

        assert retrieved.execution_mode == ToolExecutionMode.SANDBOX
        assert retrieved.sandbox_image == "test:latest"

    def test_get_unregistered_tool(self):
        """Test that unregistered tools return default metadata."""
        metadata = get_tool_metadata("nonexistent_tool")

        assert metadata.execution_mode == ToolExecutionMode.SERVER
        assert metadata.timeout_seconds == 30

    def test_list_tools_by_mode(self):
        """Test listing tools by execution mode."""
        # Register some test tools
        register_tool_metadata(
            "sandbox_test_1",
            ToolMetadata(execution_mode=ToolExecutionMode.SANDBOX),
        )
        register_tool_metadata(
            "sandbox_test_2",
            ToolMetadata(execution_mode=ToolExecutionMode.SANDBOX),
        )
        register_tool_metadata(
            "client_test_1",
            ToolMetadata(execution_mode=ToolExecutionMode.CLIENT),
        )

        sandbox_tools = list_tools_by_mode(ToolExecutionMode.SANDBOX)
        client_tools = list_tools_by_mode(ToolExecutionMode.CLIENT)

        assert "sandbox_test_1" in sandbox_tools
        assert "sandbox_test_2" in sandbox_tools
        assert "client_test_1" in client_tools
        assert "client_test_1" not in sandbox_tools


class TestDecorators:
    """Tests for execution mode decorators."""

    def test_sandbox_tool_decorator(self):
        """Test @sandbox_tool decorator registers metadata."""

        @sandbox_tool(image="python:3.12", memory="512m", timeout=120)
        async def test_sandbox_func():
            pass

        # The decorator should register metadata
        metadata = get_tool_metadata("test_sandbox_func")
        assert metadata.execution_mode == ToolExecutionMode.SANDBOX
        assert metadata.sandbox_image == "python:3.12"
        assert metadata.resource_limits.memory == "512m"
        assert metadata.timeout_seconds == 120

    def test_client_tool_decorator(self):
        """Test @client_tool decorator registers metadata."""

        @client_tool(handler="testHandler", timeout=60)
        async def test_client_func():
            pass

        metadata = get_tool_metadata("test_client_func")
        assert metadata.execution_mode == ToolExecutionMode.CLIENT
        assert metadata.client_handler == "testHandler"
        assert metadata.timeout_seconds == 60

    def test_server_tool_decorator(self):
        """Test @server_tool decorator registers metadata."""

        @server_tool(timeout=90)
        async def test_server_func():
            pass

        metadata = get_tool_metadata("test_server_func")
        assert metadata.execution_mode == ToolExecutionMode.SERVER
        assert metadata.timeout_seconds == 90


class TestExecutionRouter:
    """Tests for ExecutionRouter."""

    @pytest.fixture
    def router(self):
        """Create a router with mock executors."""
        sandbox_executor = AsyncMock()
        client_executor = AsyncMock()

        router = ExecutionRouter(
            sandbox_executor=sandbox_executor,
            client_executor=client_executor,
        )

        return router

    @pytest.fixture
    def context(self):
        """Create test execution context."""
        return ExecutionContext(
            run_id=uuid4(),
            workspace_id=uuid4(),
            user_id=uuid4(),
        )

    def test_register_tool_function(self, router):
        """Test registering a tool function."""

        async def my_tool(arg: str) -> dict:
            return {"result": arg}

        router.register_tool_function("my_tool", my_tool)
        assert "my_tool" in router._tool_functions

    @pytest.mark.asyncio
    async def test_execute_server_tool(self, router, context):
        """Test routing to server executor."""

        async def server_tool_func(message: str) -> dict:
            return {"echo": message}

        router.register_tool_function("server_test", server_tool_func)

        result = await router.execute(
            tool_name="server_test",
            tool_id="test-123",
            arguments={"message": "hello"},
            context=context,
        )

        assert result.success is True
        assert result.result == {"echo": "hello"}
        assert result.execution_mode == "server"

    @pytest.mark.asyncio
    async def test_execute_sandbox_tool(self, router, context):
        """Test routing to sandbox executor."""
        # Register metadata for sandbox tool
        register_tool_metadata(
            "sandbox_exec_test",
            ToolMetadata(
                execution_mode=ToolExecutionMode.SANDBOX,
                sandbox_image="python:3.12",
            ),
        )

        # Mock sandbox executor response
        router._sandbox_executor.execute.return_value = ToolResult(
            tool_name="sandbox_exec_test",
            tool_id="test-456",
            success=True,
            result={"output": "sandbox result"},
            execution_time_ms=100,
            execution_mode="sandbox",
        )

        result = await router.execute(
            tool_name="sandbox_exec_test",
            tool_id="test-456",
            arguments={"code": "print(1)"},
            context=context,
        )

        assert result.success is True
        assert result.execution_mode == "sandbox"
        router._sandbox_executor.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_client_tool(self, router, context):
        """Test routing to client executor."""
        # Register metadata for client tool
        register_tool_metadata(
            "client_exec_test",
            ToolMetadata(
                execution_mode=ToolExecutionMode.CLIENT,
                client_handler="testHandler",
            ),
        )

        # Mock client executor response
        router._client_executor.execute.return_value = ToolResult(
            tool_name="client_exec_test",
            tool_id="test-789",
            success=True,
            result={"file": "selected.txt"},
            execution_time_ms=500,
            execution_mode="client",
        )

        result = await router.execute(
            tool_name="client_exec_test",
            tool_id="test-789",
            arguments={"extensions": [".txt"]},
            context=context,
        )

        assert result.success is True
        assert result.execution_mode == "client"
        router._client_executor.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_unregistered_tool(self, router, context):
        """Test executing unregistered tool returns error."""
        result = await router.execute(
            tool_name="nonexistent_tool",
            tool_id="test-000",
            arguments={},
            context=context,
        )

        assert result.success is False
        assert "not registered" in result.error_message


class TestSandboxExecutor:
    """Tests for SandboxExecutor with mocked Docker."""

    @pytest.fixture
    def mock_docker(self):
        """Create mock Docker client."""
        mock_container = AsyncMock()
        mock_container.id = "test-container-123"
        mock_container.start = AsyncMock()
        mock_container.wait = AsyncMock(return_value={"StatusCode": 0})
        mock_container.log = AsyncMock(return_value=['{"success": true, "result": "ok"}'])
        mock_container.delete = AsyncMock()

        mock_docker = AsyncMock()
        mock_docker.containers.create = AsyncMock(return_value=mock_container)

        return mock_docker

    @pytest.mark.asyncio
    async def test_execute_success(self, mock_docker):
        """Test successful sandbox execution."""
        from aiwen.services.agent.tools.sandbox_executor import SandboxExecutor

        executor = SandboxExecutor()
        executor._docker = mock_docker

        context = ExecutionContext(run_id=uuid4(), workspace_id=uuid4())
        metadata = ToolMetadata(
            execution_mode=ToolExecutionMode.SANDBOX,
            sandbox_image="python:3.12-slim",
            timeout_seconds=30,
        )

        result = await executor.execute(
            tool_name="test_tool",
            tool_id="exec-123",
            arguments={"code": "print(1)"},
            metadata=metadata,
            context=context,
        )

        assert result.success is True
        assert result.execution_mode == "sandbox"
        assert result.sandbox_container_id == "test-container-123"

    @pytest.mark.asyncio
    async def test_execute_timeout(self, mock_docker):
        """Test sandbox execution timeout."""
        import asyncio

        from aiwen.services.agent.tools.sandbox_executor import SandboxExecutor

        # Make wait hang forever
        mock_docker.containers.create.return_value.wait = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        mock_docker.containers.create.return_value.kill = AsyncMock()

        executor = SandboxExecutor()
        executor._docker = mock_docker

        context = ExecutionContext(run_id=uuid4(), workspace_id=uuid4())
        metadata = ToolMetadata(
            execution_mode=ToolExecutionMode.SANDBOX,
            sandbox_image="python:3.12-slim",
            timeout_seconds=1,  # Very short timeout
        )

        result = await executor.execute(
            tool_name="test_tool",
            tool_id="exec-timeout",
            arguments={"code": "while True: pass"},
            metadata=metadata,
            context=context,
        )

        assert result.success is False
        assert "timed out" in result.error_message.lower()


class TestClientExecutor:
    """Tests for ClientExecutor with mocked Redis."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.setex = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.delete = AsyncMock()
        redis.xadd = AsyncMock()
        redis.publish = AsyncMock()

        pubsub = AsyncMock()
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.close = AsyncMock()
        pubsub.get_message = AsyncMock(return_value=None)
        redis.pubsub = MagicMock(return_value=pubsub)

        return redis

    @pytest.mark.asyncio
    async def test_execute_timeout(self, mock_redis):
        """Test client execution timeout."""
        from aiwen.services.agent.tools.client_executor import ClientExecutor

        executor = ClientExecutor(redis_client=mock_redis)

        context = ExecutionContext(run_id=uuid4(), workspace_id=uuid4())
        metadata = ToolMetadata(
            execution_mode=ToolExecutionMode.CLIENT,
            client_handler="testHandler",
            timeout_seconds=1,  # Very short timeout
        )

        result = await executor.execute(
            tool_name="test_client_tool",
            tool_id="client-123",
            arguments={},
            metadata=metadata,
            context=context,
        )

        assert result.success is False
        assert "did not respond" in result.error_message

    @pytest.mark.asyncio
    async def test_submit_result(self, mock_redis):
        """Test submitting client tool result."""
        from aiwen.services.agent.tools.client_executor import (
            PENDING_TOOL_KEY,
            ClientExecutor,
        )

        # Mock pending execution exists
        mock_redis.get = AsyncMock(return_value='{"tool_id": "client-456"}')

        executor = ClientExecutor(redis_client=mock_redis)

        result = ToolResultSubmission(
            tool_id="client-456",
            success=True,
            result={"selected": "file.txt"},
        )

        accepted = await executor.submit_result("client-456", result)

        assert accepted is True
        mock_redis.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_result_not_found(self, mock_redis):
        """Test submitting result for non-existent tool."""
        from aiwen.services.agent.tools.client_executor import ClientExecutor

        # Mock pending execution does not exist
        mock_redis.get = AsyncMock(return_value=None)

        executor = ClientExecutor(redis_client=mock_redis)

        result = ToolResultSubmission(
            tool_id="unknown-id",
            success=True,
            result={},
        )

        accepted = await executor.submit_result("unknown-id", result)

        assert accepted is False


class TestExecutionContext:
    """Tests for ExecutionContext schema."""

    def test_create_context(self):
        """Test creating execution context."""
        run_id = uuid4()
        workspace_id = uuid4()

        context = ExecutionContext(
            run_id=run_id,
            workspace_id=workspace_id,
        )

        assert context.run_id == run_id
        assert context.workspace_id == workspace_id
        assert context.user_id is None

    def test_context_with_all_fields(self):
        """Test context with all optional fields."""
        context = ExecutionContext(
            run_id=uuid4(),
            workspace_id=uuid4(),
            user_id=uuid4(),
            conversation_id=uuid4(),
            correlation_id="trace-123",
            timeout_override=60,
        )

        assert context.correlation_id == "trace-123"
        assert context.timeout_override == 60


class TestToolResult:
    """Tests for ToolResult schema."""

    def test_success_result(self):
        """Test creating successful result."""
        result = ToolResult(
            tool_name="test_tool",
            tool_id="exec-123",
            success=True,
            result={"data": [1, 2, 3]},
            execution_time_ms=150,
            execution_mode="server",
        )

        assert result.success is True
        assert result.error_message is None

    def test_error_result(self):
        """Test creating error result."""
        result = ToolResult(
            tool_name="test_tool",
            tool_id="exec-456",
            success=False,
            error_message="Connection failed",
            execution_time_ms=50,
            execution_mode="sandbox",
        )

        assert result.success is False
        assert result.error_message == "Connection failed"
