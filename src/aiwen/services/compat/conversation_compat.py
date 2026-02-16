# aiwen/services/compat/conversation_compat.py
"""Compatibility layer for old Conversation API to work with new Workspace architecture.

This module provides backward compatibility for the existing Conversation-based API
while internally using the new Workspace/Run/Event architecture.

Usage:
    - Old API endpoints can use ConversationCompat to transparently work with workspaces
    - New workspaces created via this layer will have legacy_conversation_id set
    - Existing conversations are automatically migrated on first access
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any
from uuid import UUID

from aiwen.models.conversations.conversation import Conversation
from aiwen.models.conversations.message import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.enums.runs import TriggerType
from aiwen.models.runs.run import Run
from aiwen.models.workspaces.workspace import Workspace
from aiwen.models.workspaces.workspace_member import WorkspaceMember
from aiwen.schemas.events.event_payloads import EventType
from aiwen.services.events.event_publisher import EventPublisher

logger = logging.getLogger(__name__)


def normalize_uuid_to_str(val: str | UUID) -> str:
    """Normalize a UUID value to string."""
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, str):
        try:
            UUID(val)
            return val
        except ValueError as e:
            raise ValueError(f"Invalid UUID string: {val}") from e
    raise TypeError(f"Expected UUID or str, got {type(val)}")


class ConversationCompat:
    """Compatibility layer for Conversation API.

    Provides methods that mirror the old Conversation-based API but internally
    work with Workspaces, Runs, and Events.
    """

    def __init__(self, db: AsyncSession, event_publisher: EventPublisher | None = None):
        """Initialize ConversationCompat.

        Args:
            db: SQLAlchemy async session
            event_publisher: Optional event publisher for emitting events
        """
        self.db = db
        self.event_publisher = event_publisher

    async def get_or_create_workspace_for_conversation(
        self,
        conversation_id: str | UUID,
        user_id: str | UUID,
        auto_commit: bool = False,
    ) -> Workspace:
        """Get or create a workspace for an existing conversation.

        If the conversation already has an associated workspace, return it.
        Otherwise, create a new workspace and link it.

        Args:
            conversation_id: The conversation ID
            user_id: The user ID
            auto_commit: Whether to commit

        Returns:
            Workspace instance

        Raises:
            ValueError: If conversation not found
        """
        conversation_id_str = normalize_uuid_to_str(conversation_id)
        user_id_str = normalize_uuid_to_str(user_id)

        # Check if workspace already exists for this conversation
        stmt = select(Workspace).where(
            Workspace.legacy_conversation_id == conversation_id_str
        )
        result = await self.db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if workspace:
            return workspace

        # Get the conversation
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id_str)
        conv_result = await self.db.execute(conv_stmt)
        conversation = conv_result.scalar_one_or_none()

        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id_str}")

        # Create workspace from conversation
        workspace = Workspace(
            name=conversation.name or "Migrated Conversation",
            description=conversation.summary,
            owner_id=user_id_str,
            app_id=str(conversation.app_id) if conversation.app_id else None,
            visibility="private",
            legacy_conversation_id=conversation_id_str,
            status="active",
        )
        self.db.add(workspace)
        await self.db.flush()

        # Create owner membership
        member = WorkspaceMember(
            workspace_id=str(workspace.id),
            user_id=user_id_str,
            role="owner",
            invitation_status="accepted",
            joined_at=datetime.now(UTC),
        )
        self.db.add(member)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(workspace)

        logger.info(
            f"Created workspace {workspace.id} for conversation {conversation_id_str}"
        )

        return workspace

    async def create_conversation_as_workspace(
        self,
        app_id: str | UUID,
        user_id: str | UUID,
        name: str,
        from_source: str = "api",
        mode: str = "chat",
        auto_commit: bool = False,
    ) -> tuple[Conversation, Workspace]:
        """Create a new conversation and its associated workspace.

        This creates both a legacy Conversation (for backward compatibility)
        and a new Workspace (for the new architecture).

        Args:
            app_id: The app ID
            user_id: The user ID
            name: Conversation/workspace name
            from_source: Source identifier
            mode: Conversation mode
            auto_commit: Whether to commit

        Returns:
            Tuple of (Conversation, Workspace)
        """
        app_id_str = normalize_uuid_to_str(app_id)
        user_id_str = normalize_uuid_to_str(user_id)

        # Create legacy conversation
        conversation = Conversation(
            app_id=app_id_str,
            name=name,
            status="normal",
            from_source=from_source,
            account_id=user_id_str,
            mode=mode,
        )
        self.db.add(conversation)
        await self.db.flush()

        # Create workspace
        workspace = Workspace(
            name=name,
            owner_id=user_id_str,
            app_id=app_id_str,
            visibility="private",
            legacy_conversation_id=str(conversation.id),
            status="active",
        )
        self.db.add(workspace)
        await self.db.flush()

        # Create owner membership
        member = WorkspaceMember(
            workspace_id=str(workspace.id),
            user_id=user_id_str,
            role="owner",
            invitation_status="accepted",
            joined_at=datetime.now(UTC),
        )
        self.db.add(member)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(conversation)
        await self.db.refresh(workspace)

        return conversation, workspace

    async def create_run_for_message(
        self,
        workspace_id: str | UUID,
        app_id: str | UUID,
        user_id: str | UUID,
        message_content: str,
        attachments: list[dict[str, Any]] | None = None,
        auto_commit: bool = False,
    ) -> Run:
        """Create a run for a new user message.

        This is the equivalent of sending a message in the old API.

        Args:
            workspace_id: The workspace ID
            app_id: The app ID
            user_id: The user ID
            message_content: The message content
            attachments: Optional attachments
            auto_commit: Whether to commit

        Returns:
            Created Run instance
        """
        workspace_id_str = normalize_uuid_to_str(workspace_id)
        app_id_str = normalize_uuid_to_str(app_id)
        user_id_str = normalize_uuid_to_str(user_id)

        # Create run
        run = Run(
            workspace_id=workspace_id_str,
            app_id=app_id_str,
            user_id=user_id_str,
            trigger_type=TriggerType.USER,
            input_data={
                "message": message_content,
                "attachments": attachments,
            },
            status="pending",
        )
        self.db.add(run)
        await self.db.flush()

        # Increment workspace run count
        stmt = select(Workspace).where(Workspace.id == workspace_id_str)
        result = await self.db.execute(stmt)
        workspace = result.scalar_one_or_none()
        if workspace:
            workspace.run_count += 1
            workspace.updated_at = datetime.now(UTC)

        # Emit user message event if publisher available
        if self.event_publisher:
            await self.event_publisher.publish(
                event_type=EventType.USER_MESSAGE,
                workspace_id=workspace_id_str,
                run_id=str(run.id),
                user_id=user_id_str,
                payload={
                    "content": message_content,
                    "attachments": attachments,
                },
                auto_commit=False,
            )

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(run)
        return run

    async def get_conversation_from_workspace(
        self,
        workspace_id: str | UUID,
    ) -> Conversation | None:
        """Get the legacy conversation associated with a workspace.

        Args:
            workspace_id: The workspace ID

        Returns:
            Conversation instance or None
        """
        workspace_id_str = normalize_uuid_to_str(workspace_id)

        stmt = select(Workspace).where(Workspace.id == workspace_id_str)
        result = await self.db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace or not workspace.legacy_conversation_id:
            return None

        conv_stmt = select(Conversation).where(
            Conversation.id == str(workspace.legacy_conversation_id)
        )
        conv_result = await self.db.execute(conv_stmt)
        return conv_result.scalar_one_or_none()

    async def sync_message_to_conversation(
        self,
        workspace_id: str | UUID,
        run_id: str | UUID,
        query: str,
        answer: str,
        user_id: str | UUID,
        auto_commit: bool = False,
    ) -> Message | None:
        """Sync a run's messages to the legacy conversation.

        Creates a Message record in the old format for backward compatibility.

        Args:
            workspace_id: The workspace ID
            run_id: The run ID
            query: User's query
            answer: Agent's answer
            user_id: The user ID
            auto_commit: Whether to commit

        Returns:
            Created Message or None if no legacy conversation
        """
        conversation = await self.get_conversation_from_workspace(workspace_id)
        if not conversation:
            return None

        workspace_id_str = normalize_uuid_to_str(workspace_id)
        user_id_str = normalize_uuid_to_str(user_id)

        # Get workspace for app_id
        stmt = select(Workspace).where(Workspace.id == workspace_id_str)
        result = await self.db.execute(stmt)
        workspace = result.scalar_one_or_none()

        if not workspace:
            return None

        # Create legacy message
        message = Message(
            app_id=None,
            conversation_id=str(conversation.id),
            query=query,
            answer=answer,
            message=[{"role": "user", "content": query}],
            status="normal",
            from_source="api",
            from_account_id=user_id_str,
        )
        self.db.add(message)

        # Update conversation dialogue count
        conversation.dialogue_count += 1
        conversation.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(message)
        return message

    async def list_conversations_as_workspaces(
        self,
        user_id: str | UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List conversations with their workspace info.

        Returns a combined view of conversations and their workspaces.

        Args:
            user_id: The user ID
            skip: Number to skip
            limit: Maximum number

        Returns:
            List of conversation/workspace data
        """
        user_id_str = normalize_uuid_to_str(user_id)

        # Query conversations owned by user
        stmt = (
            select(Conversation)
            .where(
                Conversation.account_id == user_id_str,
                Conversation.is_deleted == False,  # noqa: E712
            )
            .order_by(Conversation.updated_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        conversations = result.scalars().all()

        results = []
        for conv in conversations:
            # Check if workspace exists
            ws_stmt = select(Workspace).where(
                Workspace.legacy_conversation_id == str(conv.id)
            )
            ws_result = await self.db.execute(ws_stmt)
            workspace = ws_result.scalar_one_or_none()

            results.append(
                {
                    "conversation_id": str(conv.id),
                    "workspace_id": str(workspace.id) if workspace else None,
                    "name": conv.name,
                    "summary": conv.summary,
                    "status": conv.status,
                    "dialogue_count": conv.dialogue_count,
                    "created_at": conv.created_at,
                    "updated_at": conv.updated_at,
                    "migrated": workspace is not None,
                }
            )

        return results
