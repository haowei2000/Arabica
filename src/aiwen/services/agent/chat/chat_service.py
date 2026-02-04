#!/usr/bin/env python3
"""
Chat Service - 聊天服务

功能描述:
    提供聊天服务的业务逻辑，包括直接模式的聊天处理。
    负责协调对话、消息的创建，以及事件流的生成。


依赖模块:
    - chat_helper: 辅助函数
    - CRUD services: 数据库操作
    - Agent Registry: Agent 注册管理
"""

import logging

from aiwen.schemas.agents.input import TextInput
from aiwen.services.agent.chat.chat_helper import create_conversation
from aiwen.services.crud.conversation_crud import ConversationCRUD

logger = logging.getLogger(__name__)
