"""Lifecycle event handlers for tasks and artifacts.

Handles task.* and artifact.* events for lifecycle management.
These events can be published by:
- Executors (when LLM creates/updates via tools)
- API endpoints (when users manually manage)
- Triggers (when workspace rules auto-create)
"""

import logging
from uuid import UUID

from aiwen.core.enums.events import EventType
from aiwen.models.events.event import Event

logger = logging.getLogger(__name__)


async def handle_task_event(event: Event):
    """Handle task.* events - task lifecycle management.

    Event Types:
    - task.create: New task created
    - task.update: Task modified (status, assignee, description, etc.)
    - task.delete: Task deleted/archived
    - task.complete: Task marked as complete
    - task.assign: Task assigned to user

    Args:
        event: The task event
    """
    try:
        if not event.run_id:
            logger.debug(f"Received {event.event_type} without run_id")

        payload = event.payload or {}
        task_data = payload.get("task", {})
        task_id = task_data.get("id") or payload.get("task_id")

        match event.event_type:
            case EventType.TASK_CREATE:
                logger.info(f"Task created: {task_id} - {task_data.get('title', 'Untitled')}")

                # TODO: Implement task creation side effects
                # - Send notifications to assignees
                # - Update workspace task count
                # - Trigger downstream workflows
                # - Index task for search

            case EventType.TASK_UPDATE:
                logger.info(f"Task updated: {task_id}")
                changes = payload.get("changes", {})

                # TODO: Implement task update side effects
                # - Notify on status changes (pending → in_progress → completed)
                # - Notify on assignee changes
                # - Track task history
                # - Update dependent tasks

            case EventType.TASK_DELETE:
                logger.info(f"Task deleted: {task_id}")

                # TODO: Implement task deletion side effects
                # - Archive instead of hard delete
                # - Notify assignees
                # - Update workspace metrics
                # - Clean up task dependencies

            case EventType.TASK_COMPLETE:
                logger.info(f"Task completed: {task_id}")

                # TODO: Implement task completion side effects
                # - Send completion notifications
                # - Trigger dependent tasks
                # - Update progress metrics

            case EventType.TASK_ASSIGN:
                assignee = payload.get("assignee")
                logger.info(f"Task {task_id} assigned to {assignee}")

                # TODO: Implement task assignment side effects
                # - Send assignment notification
                # - Update assignee's task list

    except Exception as e:
        logger.error(f"handle_task_event error: {e}", exc_info=True)


async def handle_artifact_event(event: Event):
    """Handle artifact.* events - artifact lifecycle management.

    Artifacts are structured outputs from agent execution, such as:
    - Generated code files
    - Analysis reports
    - Visualizations/charts
    - Structured data exports

    Event Types:
    - artifact.create: New artifact generated
    - artifact.update: Artifact content modified
    - artifact.delete: Artifact removed
    - artifact.version: New version of existing artifact

    Args:
        event: The artifact event
    """
    try:
        if not event.run_id:
            logger.warning(f"Received {event.event_type} without run_id")
            return

        run_id = event.run_id if isinstance(event.run_id, UUID) else UUID(str(event.run_id))
        payload = event.payload or {}
        artifact_data = payload.get("artifact", {})
        artifact_id = artifact_data.get("id") or payload.get("artifact_id")
        artifact_type = artifact_data.get("type", "unknown")

        match event.event_type:
            case EventType.ARTIFACT_CREATE:
                logger.info(f"Artifact created: {artifact_id} (type: {artifact_type}) for run {run_id}")

                # TODO: Implement artifact creation side effects
                # - Store artifact content (S3, local storage, database)
                # - Generate preview/thumbnail for UI
                # - Index artifact metadata for search
                # - Trigger post-processing (syntax highlighting, validation)
                # - Notify subscribers (workspace members)

            case EventType.ARTIFACT_UPDATE:
                logger.info(f"Artifact updated: {artifact_id}")

                # TODO: Implement artifact update side effects
                # - Create new version (versioned artifacts)
                # - Update search index
                # - Invalidate cached previews
                # - Notify collaborators

            case EventType.ARTIFACT_DELETE:
                logger.info(f"Artifact deleted: {artifact_id}")

                # TODO: Implement artifact deletion side effects
                # - Soft delete / archive
                # - Clean up storage
                # - Remove from search index
                # - Notify references (runs, tasks that link to this artifact)

            case EventType.ARTIFACT_VERSION:
                version = payload.get("version")
                logger.info(f"Artifact versioned: {artifact_id} → v{version}")

                # TODO: Implement artifact versioning side effects
                # - Store version metadata
                # - Enable diff/comparison with previous versions
                # - Track version lineage

    except Exception as e:
        logger.error(f"handle_artifact_event error: {e}", exc_info=True)
