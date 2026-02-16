"""
Dependencies module for FastAPI dependency injection.

Provides reusable dependencies for authentication, database sessions,
and other common requirements.
"""

from aiwen.core.dependencies.auth import (
    get_current_active_user,
    get_current_user,
    get_token_data,
    oauth2_scheme,
)
from aiwen.core.dependencies.workspace import (
    EventConsumerDep,
    EventCRUDDep,
    EventPublisherDep,
    EventReplayerDep,
    RunCRUDDep,
    RunStateMachineDep,
    WorkspaceCRUDDep,
    WorkspaceMemberCRUDDep,
    get_event_consumer,
    get_event_crud,
    get_event_publisher,
    get_event_replayer,
    get_run_crud,
    get_run_state_machine,
    get_workspace_crud,
    get_workspace_member_crud,
)

__all__ = [
    "EventCRUDDep",
    "EventConsumerDep",
    "EventPublisherDep",
    "EventReplayerDep",
    "RunCRUDDep",
    "RunStateMachineDep",
    "WorkspaceCRUDDep",
    "WorkspaceMemberCRUDDep",
    "get_current_active_user",
    "get_current_user",
    "get_event_consumer",
    "get_event_crud",
    "get_event_publisher",
    "get_event_replayer",
    "get_run_crud",
    "get_run_state_machine",
    "get_token_data",
    "get_workspace_crud",
    "get_workspace_member_crud",
    "oauth2_scheme",
]
