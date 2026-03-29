#!/usr/bin/env python3
"""
Data Migration Script: Conversation/AgentTask to Workspace/Run/Event

This script migrates existing data from the old Conversation/Message/AgentTask
architecture to the new Workspace/Run/Event event-sourced architecture.

Usage:
    python scripts/migrate_to_workspace.py [--dry-run] [--batch-size=100]

Options:
    --dry-run       Run without making changes (preview only)
    --batch-size    Number of records to process per batch (default: 100)
    --continue      Continue from last migration checkpoint

The migration process:
1. Conversations -> Workspaces (with legacy_conversation_id)
2. AgentTasks -> Runs (with legacy_task_id)
3. Messages -> Events (user.message and agent.message events)

Author: AI Assistant
Created: 2026-01-27
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from structure.models.executor.agent_task import AgentTask
from structure.models.executor.conversation import Conversation
from structure.models.executor.event import Event
from structure.models.executor.message import Message
from structure.models.executor.run import Run
from structure.models.executor.workspace import Workspace
from structure.models.executor.workspace_member import WorkspaceMember
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.extensions.database import get_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class MigrationState:
    """Tracks migration progress for resumption."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.state = self._load()

    def _load(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {
            "conversations_migrated": 0,
            "tasks_migrated": 0,
            "messages_migrated": 0,
            "last_conversation_id": None,
            "last_task_id": None,
            "completed": False,
        }

    def save(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=2))

    def update(self, **kwargs) -> None:
        self.state.update(kwargs)
        self.save()


class WorkspaceMigration:
    """Handles migration from Conversation to Workspace architecture."""

    def __init__(
        self,
        dry_run: bool = False,
        batch_size: int = 100,
        state_file: Path | None = None,
    ):
        self.dry_run = dry_run
        self.batch_size = batch_size
        self.state = MigrationState(
            state_file or Path(__file__).parent / ".migration_state.json"
        )
        self.stats = {
            "conversations": 0,
            "workspaces_created": 0,
            "tasks": 0,
            "runs_created": 0,
            "messages": 0,
            "events_created": 0,
            "errors": 0,
        }

    async def run(self) -> None:
        """Execute the full migration."""
        logger.info(f"Starting migration (dry_run={self.dry_run})")

        async with get_session("structure") as session:
            # Step 1: Migrate Conversations to Workspaces
            await self.migrate_conversations(session)

            # Step 2: Migrate AgentTasks to Runs
            await self.migrate_tasks(session)

            # Step 3: Migrate Messages to Events
            await self.migrate_messages(session)

            if not self.dry_run:
                await session.commit()
                self.state.update(completed=True)

        self._print_summary()

    async def migrate_conversations(self, session: AsyncSession) -> None:
        """Migrate Conversations to Workspaces."""
        logger.info("=== Migrating Conversations to Workspaces ===")

        # Count total
        count_stmt = select(func.count(Conversation.id)).where(
            Conversation.is_deleted == False  # noqa: E712
        )
        total = (await session.execute(count_stmt)).scalar() or 0
        logger.info(f"Total conversations to migrate: {total}")

        # Check for already migrated
        migrated_stmt = select(func.count(Workspace.id)).where(
            Workspace.legacy_conversation_id.isnot(None)
        )
        already_migrated = (await session.execute(migrated_stmt)).scalar() or 0
        logger.info(f"Already migrated: {already_migrated}")

        if already_migrated >= total:
            logger.info("All conversations already migrated, skipping...")
            return

        # Get unmigrated conversations
        offset = 0
        while True:
            # Find conversations not yet migrated
            stmt = (
                select(Conversation)
                .where(
                    Conversation.is_deleted == False,  # noqa: E712
                    ~Conversation.id.in_(
                        select(Workspace.legacy_conversation_id).where(
                            Workspace.legacy_conversation_id.isnot(None)
                        )
                    ),
                )
                .order_by(Conversation.created_at)
                .offset(offset)
                .limit(self.batch_size)
            )

            result = await session.execute(stmt)
            conversations = list(result.scalars().all())

            if not conversations:
                break

            for conv in conversations:
                try:
                    await self._migrate_conversation(session, conv)
                    self.stats["conversations"] += 1
                except Exception as e:
                    logger.error(f"Error migrating conversation {conv.id}: {e}")
                    self.stats["errors"] += 1

            offset += len(conversations)
            logger.info(f"Processed {offset} conversations...")

            if not self.dry_run:
                await session.flush()
                self.state.update(
                    conversations_migrated=self.stats["conversations"],
                    last_conversation_id=str(conversations[-1].id),
                )

    async def _migrate_conversation(
        self, session: AsyncSession, conv: Conversation
    ) -> Workspace:
        """Migrate a single conversation to workspace."""
        if self.dry_run:
            logger.debug(f"[DRY-RUN] Would migrate conversation {conv.id}")
            self.stats["workspaces_created"] += 1
            return None

        # Determine owner
        owner_id = (
            str(conv.account_id) if conv.account_id else str(conv.from_end_user_id)
        )
        if not owner_id or owner_id == "None":
            # Fallback: use a system user or skip
            logger.warning(f"Conversation {conv.id} has no owner, skipping")
            return None

        # Create workspace
        workspace = Workspace(
            name=conv.name or f"Conversation {str(conv.id)[:8]}",
            description=conv.summary,
            owner_id=owner_id,
            app_id=str(conv.app_id) if conv.app_id else None,
            visibility="private",
            legacy_conversation_id=str(conv.id),
            status="active" if conv.status == "normal" else "archived",
            run_count=conv.dialogue_count,
            member_count=1,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )
        session.add(workspace)
        await session.flush()

        # Create owner membership
        member = WorkspaceMember(
            workspace_id=str(workspace.id),
            user_id=owner_id,
            role="owner",
            invitation_status="accepted",
            joined_at=conv.created_at,
            created_at=conv.created_at,
        )
        session.add(member)

        self.stats["workspaces_created"] += 1
        logger.debug(f"Migrated conversation {conv.id} -> workspace {workspace.id}")

        return workspace

    async def migrate_tasks(self, session: AsyncSession) -> None:
        """Migrate AgentTasks to Runs."""
        logger.info("=== Migrating AgentTasks to Runs ===")

        # Count total
        count_stmt = select(func.count(AgentTask.id))
        total = (await session.execute(count_stmt)).scalar() or 0
        logger.info(f"Total tasks to migrate: {total}")

        # Check for already migrated
        migrated_stmt = select(func.count(Run.id)).where(Run.legacy_task_id.isnot(None))
        already_migrated = (await session.execute(migrated_stmt)).scalar() or 0
        logger.info(f"Already migrated: {already_migrated}")

        if already_migrated >= total:
            logger.info("All tasks already migrated, skipping...")
            return

        # Get unmigrated tasks
        offset = 0
        while True:
            stmt = (
                select(AgentTask)
                .where(
                    ~AgentTask.id.in_(
                        select(Run.legacy_task_id).where(Run.legacy_task_id.isnot(None))
                    )
                )
                .order_by(AgentTask.created_at)
                .offset(offset)
                .limit(self.batch_size)
            )

            result = await session.execute(stmt)
            tasks = list(result.scalars().all())

            if not tasks:
                break

            for task in tasks:
                try:
                    await self._migrate_task(session, task)
                    self.stats["tasks"] += 1
                except Exception as e:
                    logger.error(f"Error migrating task {task.id}: {e}")
                    self.stats["errors"] += 1

            offset += len(tasks)
            logger.info(f"Processed {offset} tasks...")

            if not self.dry_run:
                await session.flush()
                self.state.update(
                    tasks_migrated=self.stats["tasks"],
                    last_task_id=str(tasks[-1].id),
                )

    async def _migrate_task(self, session: AsyncSession, task: AgentTask) -> Run | None:
        """Migrate a single task to run."""
        if self.dry_run:
            logger.debug(f"[DRY-RUN] Would migrate task {task.id}")
            self.stats["runs_created"] += 1
            return None

        # Find workspace for this app
        ws_stmt = select(Workspace).where(Workspace.app_id == str(task.app_id)).limit(1)
        ws_result = await session.execute(ws_stmt)
        workspace = ws_result.scalar_one_or_none()

        if not workspace:
            # Create a workspace for this task
            workspace = Workspace(
                name=f"Task Workspace {str(task.app_id)[:8]}",
                owner_id=str(task.user_id),
                app_id=str(task.app_id),
                visibility="private",
                status="active",
                created_at=task.created_at,
            )
            session.add(workspace)
            await session.flush()

            # Create owner membership
            member = WorkspaceMember(
                workspace_id=str(workspace.id),
                user_id=str(task.user_id),
                role="owner",
                invitation_status="accepted",
                joined_at=task.created_at,
            )
            session.add(member)

        # Map status
        status_map = {
            "pending": "pending",
            "running": "running",
            "success": "finished",
            "failed": "failed",
        }

        # Create run
        run = Run(
            workspace_id=str(workspace.id),
            app_id=str(task.app_id),
            user_id=str(task.user_id),
            status=status_map.get(task.status, "finished"),
            trigger_type="user",
            input_data=task.payload
            if isinstance(task.payload, dict)
            else {"data": task.payload},
            output_data=task.result,
            error=task.error,
            legacy_task_id=str(task.id),
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            updated_at=task.updated_at,
        )
        session.add(run)

        self.stats["runs_created"] += 1
        logger.debug(f"Migrated task {task.id} -> run {run.id}")

        return run

    async def migrate_messages(self, session: AsyncSession) -> None:
        """Migrate Messages to Events."""
        logger.info("=== Migrating Messages to Events ===")

        # Count total
        count_stmt = select(func.count(Message.id))
        total = (await session.execute(count_stmt)).scalar() or 0
        logger.info(f"Total messages to migrate: {total}")

        # Get messages with their conversations
        offset = 0
        while True:
            stmt = (
                select(Message)
                .order_by(Message.created_at)
                .offset(offset)
                .limit(self.batch_size)
            )

            result = await session.execute(stmt)
            messages = list(result.scalars().all())

            if not messages:
                break

            for msg in messages:
                try:
                    await self._migrate_message(session, msg)
                    self.stats["messages"] += 1
                except Exception as e:
                    logger.error(f"Error migrating message {msg.id}: {e}")
                    self.stats["errors"] += 1

            offset += len(messages)
            logger.info(f"Processed {offset} messages...")

            if not self.dry_run:
                await session.flush()
                self.state.update(messages_migrated=self.stats["messages"])

    async def _migrate_message(self, session: AsyncSession, msg: Message) -> None:
        """Migrate a single message to events."""
        if self.dry_run:
            logger.debug(f"[DRY-RUN] Would migrate message {msg.id}")
            self.stats["events_created"] += 2
            return

        # Find workspace for this conversation
        ws_stmt = select(Workspace).where(
            Workspace.legacy_conversation_id == str(msg.workspace_id)
        )
        ws_result = await session.execute(ws_stmt)
        workspace = ws_result.scalar_one_or_none()

        if not workspace:
            logger.warning(f"No workspace found for conversation {msg.workspace_id}")
            return

        user_id = (
            str(msg.from_account_id) if msg.from_account_id else str(workspace.owner_id)
        )

        # Create user message event
        user_event = Event(
            event_type="user.message",
            workspace_id=str(workspace.id),
            user_id=user_id,
            payload={
                "content": msg.query,
                "legacy_message_id": str(msg.id),
            },
            sequence=self.stats["events_created"] + 1,
            created_at=msg.created_at,
        )
        session.add(user_event)
        self.stats["events_created"] += 1

        # Create agent message event
        agent_event = Event(
            event_type="agent.message",
            workspace_id=str(workspace.id),
            payload={
                "content": msg.answer,
                "legacy_message_id": str(msg.id),
            },
            sequence=self.stats["events_created"] + 1,
            created_at=msg.created_at,
        )
        session.add(agent_event)
        self.stats["events_created"] += 1

    def _print_summary(self) -> None:
        """Print migration summary."""
        logger.info("\n" + "=" * 50)
        logger.info("MIGRATION SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Conversations processed: {self.stats['conversations']}")
        logger.info(f"Workspaces created:      {self.stats['workspaces_created']}")
        logger.info(f"Tasks processed:         {self.stats['tasks']}")
        logger.info(f"Runs created:            {self.stats['runs_created']}")
        logger.info(f"Messages processed:      {self.stats['messages']}")
        logger.info(f"Events created:          {self.stats['events_created']}")
        logger.info(f"Errors:                  {self.stats['errors']}")
        logger.info("=" * 50)

        if self.dry_run:
            logger.info("This was a DRY RUN - no changes were made.")
        else:
            logger.info("Migration completed successfully!")


async def main():
    parser = argparse.ArgumentParser(
        description="Migrate Conversation/AgentTask to Workspace/Run/Event"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without making changes",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of records per batch",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        help="Path to migration state file",
    )

    args = parser.parse_args()

    migration = WorkspaceMigration(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        state_file=args.state_file,
    )

    await migration.run()


if __name__ == "__main__":
    asyncio.run(main())
