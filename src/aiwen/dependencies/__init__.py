"""
Dependencies module for FastAPI dependency injection.

Provides reusable dependencies for authentication, database sessions,
and other common requirements.
"""

from aiwen.dependencies.auth import (
    get_current_active_user,
    get_current_user,
    get_token_data,
    oauth2_scheme,
)
from aiwen.dependencies.workspace import (
    get_workspace_crud,
    get_workspace_member_crud,
    get_run_crud,
    get_event_publisher,
    get_event_consumer,
    get_event_replayer,
    get_run_state_machine,
    WorkspaceCRUDDep,
    WorkspaceMemberCRUDDep,
    RunCRUDDep,
    EventPublisherDep,
    EventConsumerDep,
    EventReplayerDep,
    RunStateMachineDep,
)

__all__ = [
    # Auth dependencies
    "get_current_active_user",
    "get_current_user",
    "get_token_data",
    "oauth2_scheme",
    # Workspace dependencies
    "get_workspace_crud",
    "get_workspace_member_crud",
    "get_run_crud",
    "get_event_publisher",
    "get_event_consumer",
    "get_event_replayer",
    "get_run_state_machine",
    # Type aliases
    "WorkspaceCRUDDep",
    "WorkspaceMemberCRUDDep",
    "RunCRUDDep",
    "EventPublisherDep",
    "EventConsumerDep",
    "EventReplayerDep",
    "RunStateMachineDep",
]
