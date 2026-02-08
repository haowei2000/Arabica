from uuid import UUID

from aiwen.schemas.agents.input import TextInput
from aiwen.schemas.conversations.conversation import ConversationCreate
from aiwen.schemas.conversations.message import MessageCreate
from aiwen.utils.sse import sse
from aiwen.utils.time import utc_now


async def create_conversation(
    *,
    app_id: UUID,  # Note: agent_id is used as app_id (agent and app are merged concepts)
    text_message: TextInput,
    conversation_crud,
):
    """Get or create conversation"""
    if text_message.workspace_id:
        conversation = await conversation_crud.get_by_id(text_message.workspace_id)
        if conversation:
            return conversation
    name = text_message.conversation_name or (
        f"Chat with {app_id} - {utc_now().strftime('%Y-%m-%d %H:%M')}"
    )

    return await conversation_crud.create(
        ConversationCreate(
            app_id=app_id,
            name=name,
            status="normal",
            from_source=text_message.from_source,
            from_account_id=text_message.from_account_id,
        )
    )


async def create_message(
    *,
    app_id: UUID,  # Note: agent_id is used as app_id (agent and app are merged concepts)
    conversation_id: UUID,
    text_message: TextInput,
    message_crud,
):
    """Create initial input"""
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
