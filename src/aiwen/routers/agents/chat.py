#!/usr/bin/env python3
"""
Chat Router - 聊天路由

功能描述:
    提供聊天 API 端点，支持队列模式和直接模式。
    路由层仅处理 HTTP 相关逻辑，业务逻辑委托给 ChatService。

作者: Claude
创建日期: 2025/12/30
版本: v2.0.0

公司名称: 艾普工华(武汉)有限责任公司
版权信息: © 2025 艾普工华(武汉)有限责任公司. 保留所有权利.
"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from aiwen.dependencies.agents import (
    get_agent_runtime,
    get_app_crud,
    get_conversation_crud,
    get_message_crud,
    get_redis_client_dep,
    get_task_crud,
    get_template_crud,
)
from aiwen.dependencies.auth import get_current_user
from aiwen.schemas.agents.input import TextMessage
from aiwen.schemas.auth.user import UserResponse
from aiwen.services.agents.chat.chat_service import ChatService
from aiwen.services.agents.crud.agent_template_crud import AgentTemplateCRUD
from aiwen.services.agents.crud.app_crud import AppCRUD
from aiwen.services.agents.crud.conversation_crud import ConversationCRUD
from aiwen.services.agents.crud.message_crud import MessageCRUD
from aiwen.services.agents.crud.task_crud import TaskCRUD
from aiwen.services.agents.runtime import AgentRuntime

router = APIRouter(prefix="/chat", tags=["chat"])


# ---------- Queue-based chat ----------

@router.post("/{app_id}")
async def chat_with_agent(
    app_id: UUID,
    payload: TextMessage,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    task_crud: TaskCRUD = Depends(get_task_crud),
    conversation_crud: ConversationCRUD = Depends(get_conversation_crud),
    message_crud: MessageCRUD = Depends(get_message_crud),
    redis_client=Depends(get_redis_client_dep),
):
    """
    队列模式聊天端点

    通过 Redis 队列异步处理聊天请求，适用于生产环境。

    Args:
        app_id: 应用 ID
        payload: 聊天消息载荷
        current_user: 当前用户
        task_crud: 任务 CRUD 服务
        conversation_crud: 对话 CRUD 服务
        message_crud: 消息 CRUD 服务
        redis_client: Redis 客户端

    Returns:
        StreamingResponse: SSE 流式响应
    """
    # Fill user info
    payload.from_account_id = payload.from_account_id or current_user.id

    # Initialize chat service
    chat_service = ChatService()

    # Stream events from service
    return StreamingResponse(
        chat_service.chat_queue_based(
            app_id=app_id,
            payload=payload,
            task_crud=task_crud,
            conversation_crud=conversation_crud,
            message_crud=message_crud,
            redis_client=redis_client,

        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ---------- Direct chat ----------

@router.post("/{app_id}/direct")
async def chat_with_agent_direct(
    app_id: UUID,
    payload: TextMessage,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    app_crud: AppCRUD = Depends(get_app_crud),
    template_crud: AgentTemplateCRUD = Depends(get_template_crud),
    conversation_crud: ConversationCRUD = Depends(get_conversation_crud),
    message_crud: MessageCRUD = Depends(get_message_crud),
    runtime: AgentRuntime = Depends(get_agent_runtime),
):
    """
    直接模式聊天端点

    直接执行聊天请求，不通过队列。适用于测试或低延迟场景。

    Args:
        app_id: 应用 ID
        payload: 聊天消息载荷
        current_user: 当前用户
        app_crud: 应用 CRUD 服务
        template_crud: Agent 模板 CRUD 服务
        conversation_crud: 对话 CRUD 服务
        message_crud: 消息 CRUD 服务
        runtime: Agent 运行时

    Returns:
        StreamingResponse: SSE 流式响应
    """
    # Fill user info
    payload.from_account_id = payload.from_account_id or current_user.id

    # Initialize chat service
    chat_service = ChatService()

    # Stream events from service
    return StreamingResponse(
        chat_service.chat_direct(
            app_id=app_id,
            payload=payload,
            app_crud=app_crud,
            template_crud=template_crud,
            conversation_crud=conversation_crud,
            message_crud=message_crud,
            runtime=runtime,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
