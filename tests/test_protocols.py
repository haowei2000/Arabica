"""
Tests for Protocol Layer

Verifies that protocols are correctly defined and components satisfy them.
"""

from typing import Any, ClassVar

import pytest

from structure.core.interfaces import (
    PROTOCOL_REGISTRY,
    ExecutorProtocol,
    RegistryProtocol,
    ToolProtocol,
    is_executor,
    is_registry,
    is_tool,
)
from structure.core.interfaces.tool import (
    BaseTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
from structure.registries.core import ExecutorRegistry, ToolRegistry


class TestProtocolDefinitions:
    """Test that protocols are properly defined."""

    def test_protocol_registry_exists(self):
        """Test that PROTOCOL_REGISTRY contains all protocols."""
        assert "RegistrableProtocol" in PROTOCOL_REGISTRY
        assert "ToolProtocol" in PROTOCOL_REGISTRY
        assert "ExecutorProtocol" in PROTOCOL_REGISTRY
        assert "RegistryProtocol" in PROTOCOL_REGISTRY

    def test_protocol_metadata_structure(self):
        """Test that protocol metadata has required fields."""
        for protocol_name, info in PROTOCOL_REGISTRY.items():
            assert "description" in info
            assert isinstance(info["description"], str)
            assert "required_attributes" in info
            assert isinstance(info["required_attributes"], list)
            assert "required_methods" in info
            assert isinstance(info["required_methods"], list)

    def test_tool_protocol_requirements(self):
        """Test that ToolProtocol has expected requirements."""
        info = PROTOCOL_REGISTRY["ToolProtocol"]
        assert "METADATA" in info["required_attributes"]
        assert "InputSchema" in info["required_attributes"]
        assert "OutputSchema" in info["required_attributes"]
        assert "execute" in info["required_methods"]
        assert "validate_input" in info["required_methods"]
        assert "get_json_schema" in info["required_methods"]

    def test_executor_protocol_requirements(self):
        """Test that ExecutorProtocol has expected requirements."""
        info = PROTOCOL_REGISTRY["ExecutorProtocol"]
        assert "TEMPLATE" in info["required_attributes"]
        assert "config" in info["required_attributes"]
        assert "setup" in info["required_methods"]
        assert "run" in info["required_methods"]


class TestToolProtocolCompliance:
    """Test that tool classes satisfy ToolProtocol."""

    def test_base_tool_implements_protocol(self):
        """Test that BaseTool satisfies ToolProtocol."""

        class TestTool(BaseTool):
            METADATA: ClassVar[ToolMetadata] = ToolMetadata(
                name="test_tool",
                display_name="Test Tool",
                description="A test tool",

            )

            async def execute(self, input_data):
                return ToolOutputSchema(success=True, message="Test")

        # Check at class level
        assert is_tool(TestTool)

        # Check at instance level
        tool = TestTool()
        assert isinstance(tool, ToolProtocol)

    def test_tool_has_required_attributes(self):
        """Test that tool has all required attributes."""

        class TestTool(BaseTool):
            METADATA: ClassVar[ToolMetadata] = ToolMetadata(
                name="test_tool",
                display_name="Test Tool",
                description="A test tool",

            )

            async def execute(self, input_data):
                return ToolOutputSchema(success=True)

        assert hasattr(TestTool, "METADATA")
        assert hasattr(TestTool, "InputSchema")
        assert hasattr(TestTool, "OutputSchema")

    def test_tool_has_required_methods(self):
        """Test that tool has all required methods."""

        class TestTool(BaseTool):
            METADATA: ClassVar[ToolMetadata] = ToolMetadata(
                name="test_tool",
                display_name="Test Tool",
                description="A test tool",

            )

            async def execute(self, input_data):
                return ToolOutputSchema(success=True)

        tool = TestTool()

        # Check required methods exist and are callable
        assert callable(getattr(tool, "execute", None))
        assert callable(getattr(tool, "validate_input", None))
        assert callable(getattr(tool, "format_output", None))
        assert callable(getattr(tool, "get_json_schema", None))
        assert callable(getattr(tool, "get_metadata", None))

    def test_custom_tool_implements_protocol(self):
        """Test that a custom tool (without BaseTool) can satisfy protocol."""

        class CustomTool:
            METADATA: ClassVar[ToolMetadata] = ToolMetadata(
                name="custom",
                display_name="Custom",
                description="Custom tool",

            )

            InputSchema: ClassVar[type[ToolInputSchema]] = ToolInputSchema
            OutputSchema: ClassVar[type[ToolOutputSchema]] = ToolOutputSchema

            async def execute(self, input_data):
                return self.OutputSchema(success=True)

            async def validate_input(self, raw_input):
                return self.InputSchema(**raw_input)

            def format_output(self, output):
                return output.model_dump()

            @classmethod
            def get_json_schema(cls):
                return {"type": "function", "function": {"name": cls.METADATA.name}}

            @classmethod
            def get_langchain_schema(cls):
                return {"name": cls.METADATA.name}

            @classmethod
            def get_metadata(cls):
                return cls.METADATA

            @classmethod
            def get_execution_mode(cls):
                return cls.METADATA.execution_mode

            async def before_execute(self, input_data):
                pass

            async def after_execute(self, input_data, output):
                pass

            async def on_error(self, input_data, error):
                return self.OutputSchema(success=False, error=str(error))

            @classmethod
            def _validate_metadata(cls):
                pass

            def __repr__(self):
                return "<CustomTool>"

        # Should satisfy protocol
        tool = CustomTool()
        assert isinstance(tool, ToolProtocol)
        assert is_tool(tool)


class TestExecutorProtocolCompliance:
    """Test that executor classes satisfy ExecutorProtocol."""

    def test_executor_implements_protocol(self):
        """Test that Executor class satisfies ExecutorProtocol."""

        class TestExecutor:
            TEMPLATE: ClassVar[dict] = {
                "template_code": "TEST",
                "template_name": "Test Executor",
                "enabled": True,
            }

            def __init__(self, config: dict):
                self.config = config
                self._token_index = 0

            async def setup(self):
                pass

            async def run(self, user_message):
                return {"answer": "test"}

            async def stream(self, user_message, tool_registry):
                result = await self.run(user_message)
                yield self._emit_message(result["answer"])

            def _emit_message(self, content: str):
                return {"event_type": "message", "payload": {"content": content}}

            def _emit_token(self, token: str, *, is_final: bool = False):
                return {"event_type": "token", "payload": {"token": token}}

            def _emit_thinking(self, content: str):
                return {"event_type": "thinking", "payload": {"content": content}}

            def _emit_plan_step(
                self, step_number: int, description: str, status: str = "pending", output: str | None = None
            ):
                return {"event_type": "plan", "payload": {}}

            def _emit_tool_call(self, tool_name: str, tool_id: str, arguments: dict):
                return {"event_type": "tool_call", "payload": {}}

            def _emit_tool_result(
                self, tool_name: str, tool_id: str, result: Any, *, execution_time_ms: int | None = None
            ):
                return {"event_type": "tool_result", "payload": {}}

            def _emit_tool_error(self, tool_name: str, tool_id: str, error_message: str):
                return {"event_type": "tool_error", "payload": {}}

            def _emit_tool_pending(
                self, tool_name: str, tool_id: str, arguments: dict, reason: str = "requires_approval"
            ):
                return {"event_type": "tool_pending", "payload": {}}

            def _reset_token_index(self):
                self._token_index = 0

        executor = TestExecutor({"test": True})
        assert isinstance(executor, ExecutorProtocol)
        assert is_executor(executor)


class TestRegistryProtocolCompliance:
    """Test that registry classes satisfy RegistryProtocol."""

    def test_tool_registry_implements_protocol(self):
        """Test that ToolRegistry satisfies RegistryProtocol."""
        registry = ToolRegistry()
        assert isinstance(registry, RegistryProtocol)
        assert is_registry(registry)

    def test_executor_registry_implements_protocol(self):
        """Test that ExecutorRegistry satisfies RegistryProtocol."""
        registry = ExecutorRegistry()
        assert isinstance(registry, RegistryProtocol)
        assert is_registry(registry)

    def test_registry_has_required_attributes(self):
        """Test that registry has required attributes."""
        registry = ToolRegistry()
        assert hasattr(registry, "_registry")
        assert hasattr(registry, "_instances")
        assert hasattr(registry, "_metadata")
        assert hasattr(registry, "config")

    def test_registry_has_required_methods(self):
        """Test that registry has required methods."""
        registry = ToolRegistry()
        assert callable(getattr(registry, "register", None))
        assert callable(getattr(registry, "get", None))
        assert callable(getattr(registry, "get_instance", None))
        assert callable(getattr(registry, "list_keys", None))
        assert callable(getattr(registry, "is_registered", None))


class TestTypeChecker:
    """Test type checking functions."""

    def test_is_tool_with_valid_tool(self):
        """Test is_tool() with a valid tool."""

        class ValidTool(BaseTool):
            METADATA: ClassVar[ToolMetadata] = ToolMetadata(
                name="valid",
                display_name="Valid",
                description="Valid tool",

            )

            async def execute(self, input_data):
                return ToolOutputSchema(success=True)

        assert is_tool(ValidTool())

    def test_is_tool_with_invalid_object(self):
        """Test is_tool() with an invalid object."""

        class NotATool:
            pass

        assert not is_tool(NotATool())

    def test_is_executor_with_valid_executor(self):
        """Test is_executor() with a valid executor."""

        class ValidExecutor:
            TEMPLATE: ClassVar[dict] = {"template_code": "TEST"}

            def __init__(self, config: dict):
                self.config = config
                self._token_index = 0

            async def setup(self):
                pass

            async def run(self, user_message):
                return {"answer": "test"}

            async def stream(self, user_message, tool_registry):
                result = await self.run(user_message)
                yield {"event": "message"}

            def _emit_message(self, content: str):
                return {}

            def _emit_token(self, token: str, *, is_final: bool = False):
                return {}

            def _emit_thinking(self, content: str):
                return {}

            def _emit_plan_step(self, step_number: int, description: str, status: str = "pending", output: str | None = None):
                return {}

            def _emit_tool_call(self, tool_name: str, tool_id: str, arguments: dict):
                return {}

            def _emit_tool_result(self, tool_name: str, tool_id: str, result: Any, *, execution_time_ms: int | None = None):
                return {}

            def _emit_tool_error(self, tool_name: str, tool_id: str, error_message: str):
                return {}

            def _emit_tool_pending(self, tool_name: str, tool_id: str, arguments: dict, reason: str = "requires_approval"):
                return {}

            def _reset_token_index(self):
                pass

        assert is_executor(ValidExecutor({}))

    def test_is_registry_with_valid_registry(self):
        """Test is_registry() with a valid registry."""
        registry = ToolRegistry()
        assert is_registry(registry)


class TestProtocolWithFunctions:
    """Test using protocols in function signatures."""

    async def test_function_accepts_protocol_compliant_tool(self):
        """Test that functions can accept protocol-compliant objects."""

        async def process_tool(tool: ToolProtocol) -> dict:
            """Function that accepts any ToolProtocol-compliant object."""
            metadata = tool.get_metadata()
            return {"name": metadata.name}

        class TestTool(BaseTool):
            METADATA: ClassVar[ToolMetadata] = ToolMetadata(
                name="test",
                display_name="Test",
                description="Test",

            )

            async def execute(self, input_data):
                return ToolOutputSchema(success=True)

        tool = TestTool()
        result = await process_tool(tool)
        assert result["name"] == "test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
