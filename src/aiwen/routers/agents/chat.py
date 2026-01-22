#!/usr/bin/env python3
"""
Chat Router - 聊天路由

提供聊天相关的 API 端点：
- POST /chat/{app_id}/start - 启动聊天任务
- GET /chat/{task_id}/messages - 获取任务消息流
- POST /chat/{task_id}/cancel - 取消任务
- POST /chat/{app_id} - 队列模式聊天（组合端点，保留兼容）
- POST /chat/{app_id}/direct - 直接模式聊天
"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from aiwen.dependencies.agents import (
    get_conversation_crud,
    get_message_crud,
    get_task_consumer,
    get_task_crud,
    get_task_producer,
)
from aiwen.dependencies.auth import get_current_user
from aiwen.schemas.agents.input import TextInput
from aiwen.schemas.auth.user import UserResponse
from aiwen.services.agents.chat.chat_service import (
    StartTaskResult,
    get_messages_by_task,
    start_task,
)
from aiwen.services.agents.crud.conversation_crud import ConversationCRUD
from aiwen.services.agents.crud.message_crud import MessageCRUD
from aiwen.services.agents.crud.task_crud import AgentTaskCRUD
from aiwen.workers.task_consumer import AgentTaskConsumer
from aiwen.workers.task_producer import AgentTaskProducer

router = APIRouter(prefix="/chat", tags=["chat"])


# ---------- Start task (split endpoint) ----------

@router.post("/{app_id}/start", response_model=StartTaskResult)
async def start_chat_task(
    app_id: UUID,
    payload: TextInput,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    task_crud: AgentTaskCRUD = Depends(get_task_crud),
    conversation_crud: ConversationCRUD = Depends(get_conversation_crud),
    message_crud: MessageCRUD = Depends(get_message_crud),
    task_producer: AgentTaskProducer = Depends(get_task_producer),
) -> StartTaskResult:
    """
    启动聊天任务

    创建对话、消息和任务后立即返回任务 ID，不等待执行结果。
    客户端可以使用返回的 task_id 调用 /chat/{task_id}/messages 获取消息流，
    或调用 /chat/{task_id}/cancel 取消任务。

    Args:
        app_id: 应用 ID
        payload: 聊天消息载荷
        current_user: 当前用户
        task_crud: 任务 CRUD 服务
        conversation_crud: 对话 CRUD 服务
        message_crud: 消息 CRUD 服务
        task_producer: 任务生产者

    Returns:
        StartTaskResult: 包含 task_id, conversation_id, message_id
    """
    payload.from_account_id = payload.from_account_id or current_user.id
    return await start_task(
        app_id=app_id,
        user_id=current_user.id,
        text_input=payload,
        task_crud=task_crud,
        conversation_crud=conversation_crud,
        message_crud=message_crud,
        task_producer=task_producer,
    )


# ---------- Get task messages (split endpoint) ----------

@router.get("/{task_id}/messages")
async def stream_task_messages(
    task_id: UUID,
    task_consumer: AgentTaskConsumer = Depends(get_task_consumer),
):
    """
    获取任务消息流

    从 Redis Stream 读取任务的消息流，返回 SSE 格式的事件。
    支持的事件类型：
    - metadata: 包含 task_id
    - chunk: 消息内容片段
    - success: 任务完成
    - error: 任务失败
    - cancelled: 任务被取消

    Args:
        task_id: 任务 ID
        task_consumer: 任务消费者

    Returns:
        StreamingResponse: SSE 流式响应
    """
    return StreamingResponse(
        get_messages_by_task(
            task_id=task_id,
            task_consumer=task_consumer,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


# # ---------- Cancel task ----------
#
# @router.post("/{task_id}/cancel")
# async def cancel_chat_task(
#     task_id: UUID,
#         redis_client=Depends(get_redis_client_dep),
#     runtime: AgentRuntime = Depends(get_agent_runtime),
# ):
#     """
#     取消正在运行的聊天任务
#
#     支持队列模式和直接模式的任务取消。
#
#     Args:
#         task_id: 任务 ID
#         redis_client: Redis 客户端
#         runtime: Agent 运行时
#
#     Returns:
#         取消结果
#     """
#     success = await cancel_task(
#         task_id=task_id,
#         redis_client=redis_client,
#         runtime=runtime,
#     )
#
#     if success:
#         return {"status": "success", "input": f"Task {task_id} cancelled successfully"}
#     else:
#         return {"status": "not_found", "input": f"Task {task_id} not found or already completed"}
