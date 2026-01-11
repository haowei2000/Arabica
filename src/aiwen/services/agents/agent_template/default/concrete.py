#!/usr/bin/env python3
"""
Default Agent Template with Conversation Memory

Provides a default agent implementation with short-term conversation memory
loaded from database conversation and message tables.

默认Agent模版，支持从数据库加载短期会话级记忆
"""

import logging
from typing import Any

from langchain.agents import create_agent

from aiwen.schemas.agents.input import TextMessage
from aiwen.services.agents.agent_registry import register_agent
from aiwen.services.agents.base import BaseAgentTemplate
from .memory import get_messages_from_history, add_message_to_history

logger = logging.getLogger(__name__)


@register_agent
class DefaultAgentTemplate(BaseAgentTemplate):
    """
    Default agent with conversation memory support.

    This agent loads conversation history from the database and includes it
    in the context when processing new messages.

    默认Agent，支持从数据库加载对话历史作为上下文。
    """

    TEMPLATE = {
        "template_code": "DEFAULT001",
        "template_name": "Default Detection Agent",
        "enabled": True,
        "version": 1,
        "config": {
            "context": {
                "enabled": True,
                "sources":
                    {
                        "conversation_history": {
                            "enabled": True,
                            "max_conversations": 20
                        },
                        "message_history": {
                            "enabled": True,
                            "max_messages": 20
                        },
                        "knowledge_base": {
                            "enabled": True,
                            "kb_ids": []
                        }
                }
            },
            "model_provider": "ollama",
            "model_name": "qwen3:30b",
            "max_history_messages": 20,  # Maximum number of historical messages to load
        }
    }

    def __init__(self, config: dict):
        """
        Initialize the agent with configuration.

        Args:
            config: Configuration dictionary for the agent
                - model_provider: LLM provider (default: "ollama")
                - model_name: Model name (default: "qwen3:30b")
                - max_history_messages: Max messages to load from history (default: 20)
        """
        super().__init__(config)
        self.model_provider = config.get("model_provider", "ollama")
        self.model_name = config.get("model_name", "qwen3:30b")
        self.max_history_messages = config.get("max_history_messages", 20)

        # Initialize LLM
        from aiwen.extensions.llm.llm import get_llm
        self.llm = get_llm(self.model_name, self.model_provider)

        # Initialize agent
        self.agent = create_agent(model=self.llm)

    async def _prepare_messages(self, input_data: TextMessage) -> list:
        """
        Prepare messages including conversation history.

        Loads historical messages from database if conversation_id is provided,
        then appends the current user message.

        Args:
            input_data: Input message with optional conversation_id

        Returns:
            List of message dictionaries including history and current message

        准备消息列表，包含对话历史。
        """
        messages = []

        # Load conversation history if conversation_id is provided
        if input_data.conversation_id:
            logger.info(f"Loading history for conversation {input_data.conversation_id}")
            history = await get_messages_from_history(
                conversation_id=input_data.conversation_id,
                max_messages=self.max_history_messages
            )
            logger.info(f"Loaded {len(history)} historical messages")
        else:
            logger.info("No conversation_id provided, starting fresh conversation")

        messages.append({"role": "user", "content": input_data.query})

        return messages

    async def run(self, input_data: TextMessage | dict) -> dict[str, Any]:
        """
        Execute the agent with conversation memory.

        Loads conversation history from a database, appends the new message,
        and generates a response using the LLM.

        Args:
            input_data: Contains 'query' and optional 'conversation_id'
                - query: User's input message
                - conversation_id: UUID of existing conversation (optional)

        Returns:
            Dictionary with 'answer' key containing the agent's response

        执行Agent，支持对话记忆。
        从数据库加载对话历史，添加新消息，并生成响应。
        """
        # Convert to TextMessage if needed
        if isinstance(input_data, dict):
            input_data = TextMessage(**input_data)

        # Prepare messages with history
        messages = await self._prepare_messages(input_data)

        logger.info(f"Processing message with {len(messages)} total messages in context")

        # Use PostgresSaver for checkpointing
        # Invoke LLM with full message history
        response = await self.llm.ainvoke(messages)

        return {"answer": response.content}

    async def stream(self, input_data: TextMessage | dict):
        """
        Stream the agent's output with conversation memory.

        Loads conversation history, appends the new message, and streams
        the agent's response as it is generated.

        Args:
            input_data: Contains 'query' and optional 'conversation_id'

        Yields:
            Chunks of the agent's thinking process and response

        流式输出Agent响应，支持对话记忆。
        """
        # 验证输入数据类型
        logger.info(f"Agent.stream called with input_data type: {type(input_data)}")

        if not isinstance(input_data, (dict, TextMessage)):
            error_msg = (
                f"Invalid input_data type: expected dict or TextMessage, "
                f"got {type(input_data).__name__}. "
                f"Content: {input_data}"
            )
            logger.error(error_msg)
            raise TypeError(error_msg)

        # Convert to TextMessage if needed
        if isinstance(input_data, dict):
            logger.info(f"Converting dict to TextMessage: {input_data}")
            try:
                input_data = TextMessage(**input_data)
            except Exception as e:
                logger.error(f"Failed to convert dict to TextMessage: {e}")
                logger.error(f"Dict content: {input_data}")
                raise ValueError(f"Invalid input_data: {input_data}")

        # Prepare messages with history
        messages = await self._prepare_messages(input_data)

        logger.info(f"Streaming message with {len(messages)} total messages in context")


        # Use PostgresSaver for checkpointing
        # Stream events from agent
        full_response = ""
        async for event in self.agent.astream_events({"messages": messages}):
            if event["event"] == "on_chat_model_start":
                yield f"Input: {event['data']['input']}"
            elif event["event"] == "on_chat_model_stream":
                yield f"Token: {event['data']['chunk'].content}"
                full_response += event['data']['chunk'].content
            elif event["event"] == "on_chat_model_end":
                yield f"Full message: {event['data']['output'].content}"
            else:
                pass
