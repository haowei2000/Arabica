"""Event-related enum definitions."""

from enum import StrEnum


class EventType(StrEnum):
    """Enumeration of all event types in the system.

    Naming convention: <domain>.<action>[.<detail>]
    """

    # User events
    USER_MESSAGE = "user.message"
    USER_FEEDBACK = "user.feedback"
    # Agent events
    AGENT_TOKEN = "agent.token"
    AGENT_MESSAGE = "agent.message"
    AGENT_PLAN_STEP = "agent.plan.step"
    AGENT_QUERY = "agent.query"
    AGENT_THINKING = "agent.thinking"
    AGENT_HEARTBEAT = "agent.heartbeat"

    # Tool events
    TOOL_CALL = "tool.call"
    TOOL_PENDING = "tool.pending"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"
    TOOL_CLIENT_REQUEST = "tool.client.request"

    # Context events
    USING_CONTEXT = "context.using"
    CONTEXT_RATED = "context.rated"
    # Run lifecycle events
    RUN_CREATED = "run.created"
    RUN_STATE_CHANGE = "run.state.change"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"

    # Workspace events
    WORKSPACE_CREATED = "workspace.created"
    WORKSPACE_UPDATED = "workspace.updated"
    WORKSPACE_MEMBER_JOIN = "workspace.member.join"
    WORKSPACE_MEMBER_LEAVE = "workspace.member.leave"
    WORKSPACE_MEMBER_ROLE_CHANGE = "workspace.member.role.change"

    # Task events
    TASK_CREATE = "task.create"
    TASK_UPDATE = "task.update"
    TASK_DELETE = "task.delete"
    TASK_COMPLETE = "task.complete"
    TASK_ASSIGN = "task.assign"

    # Artifact events
    ARTIFACT_CREATE = "artifact.create"
    ARTIFACT_UPDATE = "artifact.update"
    ARTIFACT_DELETE = "artifact.delete"
    ARTIFACT_VERSION = "artifact.version"

    # System events
    SYSTEM_ERROR = "system.error"
    SYSTEM_NOTIFICATION = "system.notification"

    # Internal worker-routing event: carries the original event to the executor.
    # Published by the worker after sequence validation; consumed by the same worker
    # to actually call _forward_to_executor.  Not intended for external clients.
    TO_EXECUTOR = "to.executor"
