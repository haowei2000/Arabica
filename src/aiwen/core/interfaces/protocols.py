"""
Protocol Definitions for Registry Components

This module defines structural protocols (PEP 544) that specify the interface
contracts for components registered in various registries.

Using Protocol instead of ABC allows for:
  - Structural typing (duck typing with type safety)
  - No mandatory inheritance requirement
  - Better flexibility for third-party extensions
  - Clear interface documentation

All protocols follow the Registrable pattern which requires:
  - A class-level metadata/config attribute (METADATA, TEMPLATE, CONFIG, etc.)
  - Type-safe validation methods
  - Consistent lifecycle hooks
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncGenerator
from typing import Any, ClassVar, Protocol, runtime_checkable


# ============================================================================
# Base Registrable Protocol
# ============================================================================


@runtime_checkable
class RegistrableProtocol(Protocol):
    """
    Base protocol for all components that can be registered in a registry.

    All registrable components must provide:
      - A unique identifier (extracted via registry-specific logic)
      - Validation capability
      - String representation

    This is the minimal contract. Specific registries may require
    additional attributes (e.g., METADATA, TEMPLATE, CONFIG).
    """

    def __repr__(self) -> str:
        """Return string representation of the component."""
        ...


# ============================================================================
# Tool Protocol
# ============================================================================


@runtime_checkable
class ToolProtocol(RegistrableProtocol, Protocol):
    """
    Protocol for tool components registered in ToolRegistry.

    A tool is an executable unit that:
      - Has metadata describing its capabilities
      - Defines input/output schemas via Pydantic models
      - Provides async execution
      - Supports lifecycle hooks (before/after execution, error handling)
      - Can generate JSON schemas for LLM integration

    Both InnerTool and ExternalTool must satisfy this protocol.
    """

    # ── Required Class Attributes ────────────────────────────────────

    METADATA: ClassVar[Any]  # ToolMetadata instance
    """Tool metadata (name, description, execution_mode, etc.)"""

    InputSchema: ClassVar[type[Any]]  # type[ToolInputSchema]
    """Pydantic model defining input parameters"""

    OutputSchema: ClassVar[type[Any]]  # type[ToolOutputSchema]
    """Pydantic model defining output structure"""

    # ── Core Methods ─────────────────────────────────────────────────

    @abstractmethod
    async def execute(self, input_data: Any) -> Any:
        """
        Execute the tool with validated input.

        Args:
            input_data: Validated input conforming to InputSchema

        Returns:
            Output conforming to OutputSchema

        Raises:
            Exception: Any error during execution
        """
        ...

    async def validate_input(self, raw_input: dict[str, Any]) -> Any:
        """
        Validate raw input against InputSchema.

        Args:
            raw_input: Raw input dictionary

        Returns:
            Validated InputSchema instance

        Raises:
            ValidationError: If input is invalid
        """
        ...

    def format_output(self, output: Any) -> dict[str, Any]:
        """
        Format output as dictionary for serialization.

        Args:
            output: Output object (OutputSchema instance)

        Returns:
            Dictionary representation of output
        """
        ...

    # ── Schema Generation ────────────────────────────────────────────

    @classmethod
    def get_json_schema(cls) -> dict[str, Any]:
        """
        Get tool schema in OpenAI function calling format.

        Returns:
            OpenAI-compatible tool definition
        """
        ...

    @classmethod
    def get_langchain_schema(cls) -> dict[str, Any]:
        """
        Get tool schema in LangChain format.

        Returns:
            LangChain-compatible tool definition
        """
        ...

    # ── Metadata Access ──────────────────────────────────────────────

    @classmethod
    def get_metadata(cls) -> Any:
        """Get tool metadata instance."""
        ...

    @classmethod
    def get_execution_mode(cls) -> Any:
        """Get tool execution mode."""
        ...

    # ── Lifecycle Hooks ──────────────────────────────────────────────

    async def before_execute(self, input_data: Any) -> None:
        """
        Pre-execution hook (optional).

        Used for: permission checks, resource allocation, logging, etc.

        Args:
            input_data: Validated input
        """
        ...

    async def after_execute(self, input_data: Any, output: Any) -> None:
        """
        Post-execution hook (optional).

        Used for: cleanup, logging, notifications, statistics, etc.

        Args:
            input_data: Input that was executed
            output: Execution result
        """
        ...

    async def on_error(self, input_data: Any | None, error: Exception) -> Any:
        """
        Error handling hook (optional).

        Args:
            input_data: Input that caused the error (may be None)
            error: Exception that was raised

        Returns:
            Error response (OutputSchema instance)
        """
        ...

    # ── Validation ───────────────────────────────────────────────────

    @classmethod
    def _validate_metadata(cls) -> None:
        """
        Validate that tool metadata is complete and consistent.

        Raises:
            ValueError: If metadata is invalid
        """
        ...


# ============================================================================
# Executor Protocol
# ============================================================================


@runtime_checkable
class ExecutorProtocol(RegistrableProtocol, Protocol):
    """
    Protocol for executor (agent) components registered in ExecutorRegistry.

    An executor is an agent that:
      - Has a template defining its configuration
      - Can be set up with resources
      - Executes user messages to produce results
      - Optionally streams events during execution
      - Emits structured events for UI updates

    This protocol defines the contract that all agent executors must satisfy.
    """

    # ── Required Class Attributes ────────────────────────────────────

    TEMPLATE: ClassVar[dict[str, Any]]
    """
    Executor template metadata containing:
      - template_code: Unique identifier (e.g., "DEFAULT", "CONFLICT")
      - template_name: Human-readable name
      - enabled: Whether this executor is active
      - version: Template version
      - config: Default configuration dict
    """

    # ── Configuration ────────────────────────────────────────────────

    config: dict[str, Any]
    """Runtime configuration (passed during initialization)"""

    # ── Core Methods ─────────────────────────────────────────────────

    @abstractmethod
    async def setup(self) -> None:
        """
        Setup resources needed by the executor.

        Called once before execution begins. Use this to:
          - Initialize LLM clients
          - Load tools from registry
          - Set up state tracking
          - Allocate resources

        Raises:
            Exception: If setup fails
        """
        ...

    @abstractmethod
    async def run(self, user_message: Any) -> dict[str, Any]:
        """
        Execute to completion and return final result.

        Args:
            user_message: User message object (UserMessage schema)

        Returns:
            Final result dictionary (typically contains "answer" key)

        Raises:
            Exception: Any execution error
        """
        ...

    async def stream(
        self, user_message: Any,
    ) -> AsyncGenerator[Any, None]:
        """
        Stream typed events during execution.

        Tool dependencies are injected via the constructor (config),
        not passed as arguments to this method.

        Yields events like:
          - AGENT_TOKEN: Streaming text chunks
          - TOOL_CALL: Tool invocation
          - TOOL_RESULT: Tool execution result
          - AGENT_MESSAGE: Complete response

        Default implementation wraps run() in a single event.

        Args:
            user_message: User message object

        Yields:
            AgentEvent instances

        Raises:
            WaitingForTool: If tool requires approval
            Exception: Other execution errors
        """
        ...

    # ── Event Emission Helpers ───────────────────────────────────────
    # These methods create typed events for different stages of execution

    def _emit_token(self, token: str, *, is_final: bool = False) -> Any:
        """Emit a streaming token event."""
        ...

    def _emit_message(self, content: str) -> Any:
        """Emit a complete message event."""
        ...

    def _emit_thinking(self, content: str) -> Any:
        """Emit a reasoning/thinking trace event."""
        ...

    def _emit_plan_step(
        self,
        step_number: int,
        description: str,
        status: str = "pending",
        output: str | None = None,
    ) -> Any:
        """Emit a plan step event."""
        ...

    def _emit_tool_call(
        self, tool_name: str, tool_id: str, arguments: dict[str, Any]
    ) -> Any:
        """Emit a tool call event."""
        ...

    def _emit_tool_result(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        *,
        execution_time_ms: int | None = None,
    ) -> Any:
        """Emit a tool result event."""
        ...

    def _emit_tool_error(
        self, tool_name: str, tool_id: str, error_message: str
    ) -> Any:
        """Emit a tool error event."""
        ...

    def _emit_tool_pending(
        self,
        tool_name: str,
        tool_id: str,
        arguments: dict[str, Any],
        reason: str = "requires_approval",
    ) -> Any:
        """Emit a tool pending (waiting for approval) event."""
        ...


# ============================================================================
# Registry Protocol
# ============================================================================


@runtime_checkable
class RegistryProtocol[K, T](Protocol):
    """
    Protocol for registry classes that manage registrable components.

    A registry:
      - Stores components indexed by keys (type K)
      - Validates components before registration
      - Creates instances from components
      - Provides filtering and querying
      - Optionally syncs to database
      - Supports lifecycle hooks

    This protocol ensures all registries follow consistent patterns.
    """

    # ── Storage ──────────────────────────────────────────────────────

    _registry: dict[K, T]
    """Internal storage for registered components"""

    _instances: dict[K, Any]
    """Cache of component instances"""

    _metadata: dict[K, dict[str, Any]]
    """Additional metadata for components"""

    # ── Configuration ────────────────────────────────────────────────

    config: Any  # RegistryConfig
    """Registry configuration"""

    # ── Registration ─────────────────────────────────────────────────

    def register(
        self,
        component: T,
        key: K | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        """
        Register a component.

        Args:
            component: Component to register
            key: Optional explicit key (default: extract from component)
            metadata: Optional metadata to attach

        Returns:
            The registered component (for decorator chaining)

        Raises:
            ValueError: If component is invalid or already registered
        """
        ...

    def get(self, key: K) -> T | None:
        """
        Get registered component by key.

        Args:
            key: Component key

        Returns:
            Component or None if not found
        """
        ...

    def get_instance(self, key: K, **kwargs: Any) -> Any:
        """
        Get or create instance of component.

        Args:
            key: Component key
            **kwargs: Additional arguments for instance creation

        Returns:
            Component instance

        Raises:
            KeyError: If component not found
        """
        ...

    def unregister(self, key: K) -> None:
        """Remove a registered component."""
        ...

    def clear(self) -> None:
        """Clear all registered components (for testing)."""
        ...

    # ── Querying ─────────────────────────────────────────────────────

    def list_keys(self) -> list[K]:
        """List all registered component keys."""
        ...

    def list_components(self) -> list[T]:
        """List all registered components."""
        ...

    def is_registered(self, key: K) -> bool:
        """Check if a component is registered."""
        ...

    def filter(self, predicate: Any) -> dict[K, T]:
        """Filter components by predicate function."""
        ...

    def get_metadata(self, key: K) -> dict[str, Any] | None:
        """Get metadata for a component."""
        ...

    # ── Lifecycle ────────────────────────────────────────────────────

    def add_on_register_hook(self, hook: Any) -> None:
        """Add hook called when component is registered."""
        ...

    def add_on_retrieve_hook(self, hook: Any) -> None:
        """Add hook called when instance is retrieved."""
        ...

    # ── Database Sync ────────────────────────────────────────────────

    async def sync_to_database(self, db: Any) -> None:
        """
        Sync registry contents to database.

        Args:
            db: AsyncSession instance
        """
        ...

    # ── Statistics ───────────────────────────────────────────────────

    def get_statistics(self) -> dict[str, Any]:
        """Get registry statistics (counts, config, etc.)."""
        ...

    # ── Internal Methods (Must Implement) ────────────────────────────

    @abstractmethod
    def _validate_component(self, component: T) -> None:
        """Validate component before registration."""
        ...

    @abstractmethod
    def _extract_key(self, component: T) -> K:
        """Extract unique key from component."""
        ...

    @abstractmethod
    def _create_instance(self, key: K, component: T, **kwargs: Any) -> Any:
        """Create instance from component."""
        ...

    @abstractmethod
    async def _sync_to_database(self, db: Any) -> None:
        """Sync registry to database (implementation-specific)."""
        ...

    # ── Magic Methods ────────────────────────────────────────────────

    def __len__(self) -> int:
        """Return number of registered components."""
        ...

    def __contains__(self, key: K) -> bool:
        """Check if key is registered."""
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...


# ============================================================================
# Type Narrowing Helpers
# ============================================================================


def is_tool(obj: Any) -> bool:
    """
    Check if an object satisfies ToolProtocol.

    Args:
        obj: Object to check

    Returns:
        True if object is a valid tool
    """
    return isinstance(obj, ToolProtocol)


def is_executor(obj: Any) -> bool:
    """
    Check if an object satisfies ExecutorProtocol.

    Args:
        obj: Object to check

    Returns:
        True if object is a valid executor
    """
    return isinstance(obj, ExecutorProtocol)


def is_registry(obj: Any) -> bool:
    """
    Check if an object satisfies RegistryProtocol.

    Args:
        obj: Object to check

    Returns:
        True if object is a valid registry
    """
    return isinstance(obj, RegistryProtocol)


# ============================================================================
# Protocol Metadata (for documentation/introspection)
# ============================================================================


PROTOCOL_REGISTRY = {
    "RegistrableProtocol": {
        "description": "Base protocol for all registrable components",
        "required_attributes": [],
        "required_methods": ["__repr__"],
    },
    "ToolProtocol": {
        "description": "Protocol for tool components",
        "required_attributes": ["METADATA", "InputSchema", "OutputSchema"],
        "required_methods": [
            "execute",
            "validate_input",
            "format_output",
            "get_json_schema",
            "get_metadata",
        ],
        "optional_methods": ["before_execute", "after_execute", "on_error"],
    },
    "ExecutorProtocol": {
        "description": "Protocol for executor (agent) components",
        "required_attributes": ["TEMPLATE", "config"],
        "required_methods": ["setup", "run"],
        "optional_methods": ["stream"],
    },
    "RegistryProtocol": {
        "description": "Protocol for registry implementations",
        "required_attributes": ["_registry", "_instances", "_metadata", "config"],
        "required_methods": [
            "register",
            "get",
            "get_instance",
            "_validate_component",
            "_extract_key",
            "_create_instance",
        ],
    },
}


__all__ = [
    # Base
    "RegistrableProtocol",
    # Specific Protocols
    "ToolProtocol",
    "ExecutorProtocol",
    "RegistryProtocol",
    # Type Checkers
    "is_tool",
    "is_executor",
    "is_registry",
    # Metadata
    "PROTOCOL_REGISTRY",
]
