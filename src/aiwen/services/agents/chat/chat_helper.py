from decimal import Decimal
from uuid import UUID

from aiwen.schemas.agents.conversation import ConversationCreate
from aiwen.schemas.agents.input import TextMessage
from aiwen.schemas.agents.message import MessageCreate, MessageUpdate
from aiwen.utils.sse import sse
from aiwen.utils.time import utc_now


async def prepare_conversation(
        *,
        app_id: UUID,  # Note: agent_id is used as app_id (agent and app are merged concepts)
        payload:TextMessage,
        conversation_crud,
):
    """Get or create conversation"""
    if payload.conversation_id:
        conversation = await conversation_crud.get_by_id(payload.conversation_id)
        if conversation:
            return conversation
    name = payload.conversation_name or (
        f"Chat with {app_id} - {utc_now().strftime('%Y-%m-%d %H:%M')}"
    )

    return await conversation_crud.create(
        ConversationCreate(
            app_id=app_id,
            name=name,
            status="normal",
            from_source=payload.from_source,
            from_account_id=payload.from_account_id,
        )
    )


async def create_message(
        *,
        app_id: UUID,  # Note: agent_id is used as app_id (agent and app are merged concepts)
        conversation_id: UUID,
        text_message:TextMessage,
        message_crud,
):
    """Create initial message"""
    return await message_crud.create(
        MessageCreate(
            app_id=app_id,  # Use agent_id as app_id (agent and app are merged concepts)
            conversation_id=conversation_id,
            query=text_message.query,
            message={
                "query": text_message.query,
                "timestamp": utc_now().isoformat(),
            },
            answer="",
            from_source=text_message.from_source,
            from_account_id=text_message.from_account_id,
        )
    )


async def stream_and_finalize(
        *,
        stream_iter,
        conversation,
        message,
        message_crud,
        conversation_crud,
):
    """Unified SSE streaming + DB finalize logic"""
    collected_chunks: list[str] = []

    yield sse(
        "metadata",
        {
            "conversation_id": str(conversation.id),
            "message_id": str(message.id),
        },
    )
    yield sse("status", "Agent is processing your request...")

    async for item in stream_iter:
        # item can be raw chunk or SSE-ready string
        if isinstance(item, str) and item.startswith("data:"):
            yield item
            continue

        collected_chunks.append(item)
        yield sse("chunk", item)

    # await conversation_crud.increment_dialogue_count(str(conversation.id))

    yield sse("success", "Agent completed processing")
