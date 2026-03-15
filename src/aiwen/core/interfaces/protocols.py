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

from aiwen.models.events import Event

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
    """Tool metadata (name, description, category, etc.)"""

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
    Protocol for executor (agent) components.

    This is the **consumer-facing interface** that the Worker, Runtime,
    and Factory depend on.  Concrete executors inherit from the
    ``Executor`` ABC (in ``executor.py``) which satisfies this protocol.

    An executor:
      - Has a template defining its configuration
      - Can be set up with resources
      - Executes user messages to produce results
      - Optionally streams events during execution
      - Supports graceful cancellation
    """

    # ── Required Class Attributes ────────────────────────────────────

    TEMPLATE: ClassVar[dict[str, Any]]
    """Executor template metadata."""

    WaitingForTool: ClassVar[type[Exception]]
    """Exception class raised when a tool requires human approval."""

    # ── Configuration ────────────────────────────────────────────────

    config: dict[str, Any]
    """Runtime configuration (passed during initialization)"""

    async def process_event(self, event: Event) -> AsyncGenerator[Event, None]:
        """Dispatch an incoming event to the matching _process_* handler."""
        yield  # pragma: no cover – protocol stub

    async def process_events(self, events: list[Event]) -> AsyncGenerator[Event, None]:
        """Process a pre-fetched run event history list.

        The last triggering event in the list drives handler dispatch.
        Implementations should use the list as the conversation history to
        avoid an extra DB round-trip inside the executor.
        """
        yield  # pragma: no cover – protocol stub

    # User event handlers

    async def _process_user_message(self, payload: dict[str, Any]) -> None:
        """Handle an incoming USER_MESSAGE event."""
        ...

    async def _process_user_feedback(self, payload: dict[str, Any]) -> None:
        """Handle an incoming USER_FEEDBACK event."""
        ...

    # Agent event handlers

    async def _process_token(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_TOKEN event."""
        ...

    async def _process_message(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_MESSAGE event."""
        ...

    async def _process_thinking(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_THINKING event."""
        ...

    async def _process_plan_step(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_PLAN_STEP event."""
        ...

    async def _process_heartbeat(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_HEARTBEAT event."""
        ...

    # Tool event handlers

    async def _process_tool_call(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TOOL_CALL event."""
        ...

    async def _process_tool_result(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TOOL_RESULT event."""
        ...

    async def _process_tool_error(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TOOL_ERROR event."""
        ...

    async def _process_tool_pending(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TOOL_PENDING event."""
        ...

    async def _process_tool_client_request(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TOOL_CLIENT_REQUEST event."""
        ...

    # Context event handlers

    async def _process_using_context(self, payload: dict[str, Any]) -> None:
        """Handle an incoming USING_CONTEXT event."""
        ...

    async def _process_put_outcome(self, payload: dict[str, Any]) -> None:
        """Handle an incoming PUT_OUTCOME event."""
        ...

    # Run lifecycle event handlers

    async def _process_run_created(self, payload: dict[str, Any]) -> None:
        """Handle an incoming RUN_CREATED event."""
        ...

    async def _process_run_state_change(self, payload: dict[str, Any]) -> None:
        """Handle an incoming RUN_STATE_CHANGE event."""
        ...

    async def _process_run_completed(self, payload: dict[str, Any]) -> None:
        """Handle an incoming RUN_COMPLETED event."""
        ...

    async def _process_run_failed(self, payload: dict[str, Any]) -> None:
        """Handle an incoming RUN_FAILED event."""
        ...

    async def _process_run_cancelled(self, payload: dict[str, Any]) -> None:
        """Handle an incoming RUN_CANCELLED event."""
        ...

    # Workspace event handlers

    async def _process_workspace_created(self, payload: dict[str, Any]) -> None:
        """Handle an incoming WORKSPACE_CREATED event."""
        ...

    async def _process_workspace_updated(self, payload: dict[str, Any]) -> None:
        """Handle an incoming WORKSPACE_UPDATED event."""
        ...

    async def _process_workspace_member_join(self, payload: dict[str, Any]) -> None:
        """Handle an incoming WORKSPACE_MEMBER_JOIN event."""
        ...

    async def _process_workspace_member_leave(self, payload: dict[str, Any]) -> None:
        """Handle an incoming WORKSPACE_MEMBER_LEAVE event."""
        ...

    async def _process_workspace_member_role_change(self, payload: dict[str, Any]) -> None:
        """Handle an incoming WORKSPACE_MEMBER_ROLE_CHANGE event."""
        ...

    # System event handlers

    async def _process_system_error(self, payload: dict[str, Any]) -> None:
        """Handle an incoming SYSTEM_ERROR event."""
        ...

    async def _process_system_notification(self, payload: dict[str, Any]) -> None:
        """Handle an incoming SYSTEM_NOTIFICATION event."""
        ...

    # ── Event Emission Helpers ───────────────────────────────────────

    # Agent events

    def _emit_token(self, token: str, *, is_final: bool = False) -> Any:
        """Emit a streaming token event (AGENT_TOKEN)."""
        ...

    def _emit_message(self, content: str) -> Any:
        """Emit a complete message event (AGENT_MESSAGE)."""
        ...

    def _emit_thinking(self, content: str) -> Any:
        """Emit a reasoning/thinking trace event (AGENT_THINKING)."""
        ...

    def _emit_plan_step(
        self,
        step_number: int,
        description: str,
        status: str = "pending",
        output: str | None = None,
    ) -> Any:
        """Emit a plan step event (AGENT_PLAN_STEP)."""
        ...

    def _emit_heartbeat(self, *, status: str = "alive", detail: str | None = None) -> Any:
        """Emit a periodic liveness signal (AGENT_HEARTBEAT)."""
        ...

    # Tool events

    def _emit_tool_call(
        self, tool_name: str, tool_id: str, arguments: dict[str, Any]
    ) -> Any:
        """Emit a tool call event (TOOL_CALL)."""
        ...

    def _emit_tool_result(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        *,
        execution_time_ms: int | None = None,
    ) -> Any:
        """Emit a tool result event (TOOL_RESULT)."""
        ...

    def _emit_tool_error(
        self, tool_name: str, tool_id: str, error_message: str
    ) -> Any:
        """Emit a tool error event (TOOL_ERROR)."""
        ...

    def _emit_tool_pending(
        self,
        tool_name: str,
        tool_id: str,
        arguments: dict[str, Any],
        reason: str = "requires_approval",
    ) -> Any:
        """Emit a tool pending (waiting for approval) event (TOOL_PENDING)."""
        ...

    def _emit_tool_client_request(
        self,
        tool_name: str,
        tool_id: str,
        handler: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: int = 120,
        config: dict[str, Any] | None = None,
    ) -> Any:
        """Emit a client-side tool execution request (TOOL_CLIENT_REQUEST)."""
        ...

    # User events

    def _emit_user_message(self, message: str, *, user_id: str | None = None) -> Any:
        """Emit a user message echo event (USER_MESSAGE)."""
        ...

    def _emit_user_feedback(
        self, feedback: str, *, rating: int | None = None, user_id: str | None = None
    ) -> Any:
        """Emit a user feedback event (USER_FEEDBACK)."""
        ...

    # Context events

    def _emit_using_context(
        self,
        context_id: str,
        context_type: str,
        name: str,
        *,
        snippet: str | None = None,
    ) -> Any:
        """Emit a context reference event (USING_CONTEXT)."""
        ...

    def _emit_put_outcome(
        self,
        context_type: str,
        name: str,
        content: str,
        *,
        context_id: str | None = None,
    ) -> Any:
        """Emit a context write-back event (PUT_OUTCOME)."""
        ...

    # Run lifecycle events

    def _emit_run_created(self, run_id: str, *, executor_code: str | None = None) -> Any:
        """Emit a run-created event (RUN_CREATED)."""
        ...

    def _emit_run_state_change(
        self,
        previous_state: str,
        new_state: str,
        *,
        reason: str | None = None,
        triggered_by: str | None = None,
    ) -> Any:
        """Emit a run state change event (RUN_STATE_CHANGE)."""
        ...

    def _emit_run_completed(
        self, run_id: str, *, result: dict[str, Any] | None = None
    ) -> Any:
        """Emit a run completed event (RUN_COMPLETED)."""
        ...

    def _emit_run_failed(
        self, run_id: str, error: str, *, error_type: str | None = None
    ) -> Any:
        """Emit a run failed event (RUN_FAILED)."""
        ...

    def _emit_run_cancelled(self, run_id: str, *, reason: str | None = None) -> Any:
        """Emit a run cancelled event (RUN_CANCELLED)."""
        ...

    # Workspace events

    def _emit_workspace_created(self, workspace_id: str, name: str) -> Any:
        """Emit a workspace-created event (WORKSPACE_CREATED)."""
        ...

    def _emit_workspace_updated(
        self, workspace_id: str, *, changes: dict[str, Any] | None = None
    ) -> Any:
        """Emit a workspace updated event (WORKSPACE_UPDATED)."""
        ...

    def _emit_workspace_member_join(
        self,
        workspace_id: str,
        member_id: str,
        role: str,
        *,
        invited_by: str | None = None,
    ) -> Any:
        """Emit a workspace member join event (WORKSPACE_MEMBER_JOIN)."""
        ...

    def _emit_workspace_member_leave(
        self, workspace_id: str, member_id: str
    ) -> Any:
        """Emit a workspace member leave event (WORKSPACE_MEMBER_LEAVE)."""
        ...

    def _emit_workspace_member_role_change(
        self,
        workspace_id: str,
        member_id: str,
        old_role: str,
        new_role: str,
    ) -> Any:
        """Emit a workspace member role change event (WORKSPACE_MEMBER_ROLE_CHANGE)."""
        ...

    # System events

    def _emit_system_error(
        self, error: str, *, error_type: str | None = None, details: dict[str, Any] | None = None
    ) -> Any:
        """Emit a system error event (SYSTEM_ERROR)."""
        ...

    def _emit_system_notification(
        self, message: str, *, level: str = "info", category: str | None = None
    ) -> Any:
        """Emit a system notification event (SYSTEM_NOTIFICATION)."""
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
            ValueError: If the component is invalid or already registered
        """
        ...

    def get(self, key: K) -> T | None:
        """
        Get a registered component by key.

        Args:
            key: Component key

        Returns:
            Component or None if not found
        """
        ...

    def get_instance(self, key: K, **kwargs: Any) -> Any:
        """
        Get or create instance of a component.

        Args:
            key: Component key
            **kwargs: Additional arguments for instance creation

        Returns:
            Component instance

        Raises:
            KeyError: If a component is not found
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
        """Add a hook called when the component is registered."""
        ...

    def add_on_retrieve_hook(self, hook: Any) -> None:
        """Add a hook called when the instance is retrieved."""
        ...

    # ── Database Sync ────────────────────────────────────────────────

    async def sync_to_database(self, db: Any) -> None:
        """
        Sync registry contents to a database.

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
        """Create instance from a component."""
        ...

    @abstractmethod
    async def _sync_to_database(self, db: Any) -> None:
        """Sync registry to a database (implementation-specific)."""
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
        True if an object is a valid executor
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
    # Metadata
    "PROTOCOL_REGISTRY",
    "ExecutorProtocol",
    # Base
    "RegistrableProtocol",
    "RegistryProtocol",
    # Specific Protocols
    "ToolProtocol",
    "is_executor",
    "is_registry",
    # Type Checkers
    "is_tool",
]
