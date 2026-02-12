"""
Protocol Usage Examples

This module demonstrates how to use the protocol layer for type-safe
component registration and validation.
"""

from typing import Any, ClassVar

from aiwen.core.interfaces.protocols import (
    ExecutorProtocol,
    ToolProtocol,
    is_executor,
    is_tool,
    PROTOCOL_REGISTRY,
)
from aiwen.core.interfaces.tool import (
    BaseTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
from aiwen.registries.core import ToolRegistry, register_tool


# ============================================================================
# Example 1: Using ToolProtocol for Type Annotations
# ============================================================================


async def execute_any_tool(tool: ToolProtocol, input_data: dict) -> dict:
    """
    Execute any tool-like object.

    This function accepts any object that implements ToolProtocol,
    not just BaseTool instances. This provides maximum flexibility.

    Args:
        tool: Any object implementing ToolProtocol
        input_data: Input parameters

    Returns:
        Tool execution result
    """
    # Type checker ensures tool has these methods
    validated_input = await tool.validate_input(input_data)
    output = await tool.execute(validated_input)
    return tool.format_output(output)


# ============================================================================
# Example 2: Creating a Tool Using BaseTool (Recommended)
# ============================================================================


@register_tool
class ExampleCalculatorTool(BaseTool):
    """
    Standard way to create tools - inherit from BaseTool.

    BaseTool implements ToolProtocol and provides helper methods.
    """

    METADATA: ClassVar[ToolMetadata] = ToolMetadata(
        name="example_calculator",
        display_name="Example Calculator",
        description="Performs basic calculations",

        category="utility",
        tags=["math", "calculator"],
    )

    class InputSchema(ToolInputSchema):
        operation: str
        a: float
        b: float

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Execute calculation."""
        operations = {
            "add": input_data.a + input_data.b,
            "subtract": input_data.a - input_data.b,
            "multiply": input_data.a * input_data.b,
            "divide": input_data.a / input_data.b if input_data.b != 0 else None,
        }

        result = operations.get(input_data.operation)
        if result is None:
            return ToolOutputSchema(
                success=False,
                error=f"Invalid operation: {input_data.operation}",
            )

        return ToolOutputSchema(
            success=True,
            message=f"Calculated: {input_data.a} {input_data.operation} {input_data.b}",
            data={"result": result},
        )


# ============================================================================
# Example 3: Implementing ToolProtocol Without Inheritance (Advanced)
# ============================================================================


class CustomTool:
    """
    Custom tool that implements ToolProtocol without inheriting from BaseTool.

    This demonstrates structural typing - the tool satisfies ToolProtocol
    by implementing all required methods and attributes, not by inheritance.

    Note: This is for demonstration. In practice, prefer inheriting from BaseTool.
    """

    METADATA: ClassVar[ToolMetadata] = ToolMetadata(
        name="custom_tool",
        display_name="Custom Tool",
        description="A custom tool without BaseTool inheritance",

    )

    InputSchema: ClassVar[type[ToolInputSchema]] = ToolInputSchema
    OutputSchema: ClassVar[type[ToolOutputSchema]] = ToolOutputSchema

    async def execute(self, input_data: Any) -> Any:
        """Execute the tool."""
        return self.OutputSchema(
            success=True,
            message="Custom tool executed",
            data={"custom": True},
        )

    async def validate_input(self, raw_input: dict[str, Any]) -> Any:
        """Validate input."""
        return self.InputSchema(**raw_input)

    def format_output(self, output: Any) -> dict[str, Any]:
        """Format output."""
        return output.model_dump()

    @classmethod
    def get_json_schema(cls) -> dict[str, Any]:
        """Get JSON schema."""
        return {
            "type": "function",
            "function": {
                "name": cls.METADATA.name,
                "description": cls.METADATA.description,
                "parameters": cls.InputSchema.model_json_schema(),
            },
        }

    @classmethod
    def get_langchain_schema(cls) -> dict[str, Any]:
        """Get LangChain schema."""
        return {
            "name": cls.METADATA.name,
            "description": cls.METADATA.description,
            "args_schema": cls.InputSchema,
        }

    @classmethod
    def get_metadata(cls) -> ToolMetadata:
        """Get metadata."""
        return cls.METADATA

    async def before_execute(self, input_data: Any) -> None:
        """Pre-execution hook."""
        pass

    async def after_execute(self, input_data: Any, output: Any) -> None:
        """Post-execution hook."""
        pass

    async def on_error(self, input_data: Any | None, error: Exception) -> Any:
        """Error handling hook."""
        return self.OutputSchema(
            success=False,
            error=str(error),
        )

    @classmethod
    def _validate_metadata(cls) -> None:
        """Validate metadata."""
        if not cls.METADATA.name:
            raise ValueError("Tool name is required")

    def __repr__(self) -> str:
        """String representation."""
        return f"<CustomTool(name='{self.METADATA.name}')>"


# ============================================================================
# Example 4: Runtime Protocol Validation
# ============================================================================


def validate_component(component: Any) -> dict[str, bool]:
    """
    Validate if a component implements various protocols.

    Args:
        component: Component to validate

    Returns:
        Dict indicating which protocols are satisfied
    """
    return {
        "is_tool": is_tool(component),
        "is_executor": is_executor(component),
        "implements_tool_protocol": isinstance(component, ToolProtocol),
        "implements_executor_protocol": isinstance(component, ExecutorProtocol),
    }


# ============================================================================
# Example 5: Protocol Introspection
# ============================================================================


def inspect_protocol(protocol_name: str) -> None:
    """
    Inspect protocol requirements.

    Args:
        protocol_name: Name of the protocol to inspect
    """
    if protocol_name not in PROTOCOL_REGISTRY:
        print(f"Protocol '{protocol_name}' not found")
        return

    info = PROTOCOL_REGISTRY[protocol_name]
    print(f"\n=== {protocol_name} ===")
    print(f"Description: {info['description']}")
    print(f"\nRequired Attributes:")
    for attr in info.get("required_attributes", []):
        print(f"  - {attr}")
    print(f"\nRequired Methods:")
    for method in info.get("required_methods", []):
        print(f"  - {method}()")
    print(f"\nOptional Methods:")
    for method in info.get("optional_methods", []):
        print(f"  - {method}()")


# ============================================================================
# Example 6: Using Tools with Protocol Type Hints
# ============================================================================


class ToolExecutor:
    """
    Tool executor that works with any ToolProtocol-compliant object.
    """

    def __init__(self, tool_registry: ToolRegistry):
        self.tool_registry = tool_registry

    async def execute_tool_by_name(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute a tool by name.

        Args:
            tool_name: Name of the tool
            input_data: Input parameters

        Returns:
            Tool execution result
        """
        # Get tool instance from registry
        tool = self.tool_registry.get_instance(tool_name)

        # Type checker knows tool implements ToolProtocol
        if not is_tool(tool):
            return {
                "success": False,
                "error": f"'{tool_name}' does not implement ToolProtocol",
            }

        # Execute with protocol methods
        return await execute_any_tool(tool, input_data)

    def list_compatible_tools(self) -> list[str]:
        """
        List all tools that satisfy ToolProtocol.

        Returns:
            List of tool names
        """
        compatible_tools = []
        for tool_name in self.tool_registry.list_keys():
            tool_class = self.tool_registry.get(tool_name)
            if tool_class and is_tool(tool_class):
                compatible_tools.append(tool_name)
        return compatible_tools


# ============================================================================
# Example Usage
# ============================================================================


async def main():
    """
    Demonstrate protocol usage.
    """
    print("=== Protocol Usage Examples ===\n")

    # 1. Inspect protocols
    print("1. Protocol Introspection:")
    inspect_protocol("ToolProtocol")

    # 2. Validate components
    print("\n2. Component Validation:")
    calculator = ExampleCalculatorTool()
    validation = validate_component(calculator)
    print(f"Calculator tool validation: {validation}")

    custom = CustomTool()
    validation = validate_component(custom)
    print(f"Custom tool validation: {validation}")

    # 3. Execute tools with protocol type hints
    print("\n3. Tool Execution:")
    result = await execute_any_tool(
        calculator,
        {"operation": "add", "a": 10, "b": 5},
    )
    print(f"Calculator result: {result}")

    # 4. Registry integration
    print("\n4. Registry Integration:")
    from aiwen.registries.manager import get_registry

    registry = get_registry(ToolRegistry)
    executor = ToolExecutor(registry)

    # List compatible tools
    compatible = executor.list_compatible_tools()
    print(f"Compatible tools in registry: {compatible}")

    # Execute via registry
    if "example_calculator" in compatible:
        result = await executor.execute_tool_by_name(
            "example_calculator",
            {"operation": "multiply", "a": 7, "b": 8},
        )
        print(f"Execution via registry: {result}")

    print("\n=== Examples Complete ===")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
