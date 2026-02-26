#!/usr/bin/env python3
"""
Agent with Server Tools Configuration Example

Demonstrates different ways to configure server-side tools:
1. Enable all server tools
2. Enable specific tool groups
3. Fine-grained control with dict config
4. Combining with browser tools and user tools
"""

import asyncio
import logging
from uuid import uuid4

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def example_1_all_server_tools():
    """Example 1: Enable all server tools"""
    from aiwen.plugins.executors.default import (
        DefaultAgentTemplate,
    )
    from aiwen.schemas.events.event_payloads import UserMessage

    logger.info("\n" + "=" * 80)
    logger.info("Example 1: Enable All Server Tools")
    logger.info("=" * 80)

    config = {
        "model_provider": "tongyi",
        "model_name": "qwen-plus",
        "enable_browser_tools": False,
        "server_tools": True,  # Enable all server tools
    }

    agent = DefaultAgentTemplate(config=config)
    logger.info("✓ Agent initialized with all server tools")

    # Test: Get current time
    message = UserMessage(message="What time is it now? Use the get_current_time tool.")

    result = await agent.run(message)
    logger.info(f"Agent response: {result['answer']}")


async def example_2_specific_groups():
    """Example 2: Enable specific tool groups"""
    from aiwen.plugins.executors.default import (
        DefaultAgentTemplate,
    )
    from aiwen.schemas.events.event_payloads import UserMessage

    logger.info("\n" + "=" * 80)
    logger.info("Example 2: Enable Specific Tool Groups")
    logger.info("=" * 80)

    config = {
        "model_provider": "tongyi",
        "model_name": "qwen-plus",
        "enable_browser_tools": False,
        "server_tools": ["context", "utility"],  # Only context and utility tools
    }

    agent = DefaultAgentTemplate(config=config)
    logger.info("✓ Agent initialized with CONTEXT_TOOLS and UTILITY_TOOLS")

    # Test: Get current time (utility tool)
    message = UserMessage(
        message="What's the current server time in Asia/Shanghai timezone?"
    )

    result = await agent.run(message)
    logger.info(f"Agent response: {result['answer']}")


async def example_3_fine_grained_control():
    """Example 3: Fine-grained control with dict"""
    from aiwen.plugins.executors.default import (
        DefaultAgentTemplate,
    )
    from aiwen.schemas.events.event_payloads import UserMessage

    logger.info("\n" + "=" * 80)
    logger.info("Example 3: Fine-Grained Control")
    logger.info("=" * 80)

    config = {
        "model_provider": "tongyi",
        "model_name": "qwen-plus",
        "enable_browser_tools": False,
        "server_tools": {
            "context": True,  # Enable context tools
            "file": False,  # Disable file tools
            "utility": True,  # Enable utility tools
        },
    }

    agent = DefaultAgentTemplate(config=config)
    logger.info("✓ Agent initialized with context + utility tools only")

    # Test: Cache operations (utility tool)
    message = UserMessage(
        message="Store the value 'Hello World' in cache with key 'greeting'. "
        "Then retrieve it to verify."
    )

    result = await agent.run(message)
    logger.info(f"Agent response: {result['answer']}")


async def example_4_combined_tools():
    """Example 4: Combining browser, server, and user tools"""
    from aiwen.services.context.tools.dynamic_tool_loader import DynamicToolLoader

    from aiwen.extensions.database import get_session
    from aiwen.plugins.executors.default import (
        DefaultAgentTemplate,
    )
    from aiwen.schemas.context.tools.user_tool import UserToolCreate
    from aiwen.schemas.events.event_payloads import UserMessage
    from aiwen.services.context.tools.tool_crud import UserToolCRUD

    logger.info("\n" + "=" * 80)
    logger.info("Example 4: Combining All Tool Types")
    logger.info("=" * 80)

    user_id = uuid4()

    async with get_session("aiwen") as db:
        # Create a user tool
        crud = UserToolCRUD(db)
        loader = DynamicToolLoader(db)

        calculator_tool = UserToolCreate(
            name="simple_calculator",
            display_name="Simple Calculator",
            description="Add two numbers together",
            execution_mode="server_run",
            category="math",
            timeout=5,
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"},
                },
                "required": ["a", "b"],
            },
            code="result = {'sum': input_data['a'] + input_data['b']}",
        )

        tool = await crud.create_tool(user_id, calculator_tool)
        logger.info(f"✓ Created user tool: {tool.name}")

        # Load user tools
        await loader.load_user_tools(user_id)
        logger.info("✓ Loaded user tools")

        # Initialize agent with all tool types
        config = {
            "model_provider": "tongyi",
            "model_name": "qwen-plus",
            "enable_browser_tools": True,  # Browser tools
            "server_tools": ["utility"],  # Server utility tools
            # User tools loaded via ToolRegistry
        }

        agent = DefaultAgentTemplate(config=config)
        logger.info("✓ Agent initialized with browser + server + user tools")

        # Test: Use multiple tool types
        message = UserMessage(
            message="Calculate 25 + 17 using simple_calculator, "
            "then tell me the current time using get_current_time."
        )

        result = await agent.run(message)
        logger.info(f"Agent response: {result['answer']}")

        # Cleanup
        await crud.delete_tool(tool.id, user_id)
        await loader.unload_tool(tool.id)
        logger.info("✓ Cleaned up user tools")


async def example_5_workspace_query():
    """Example 5: Using workspace and run history tools"""
    from sqlalchemy import select

    from aiwen.extensions.database import get_session
    from aiwen.models.workspaces.workspace import Workspace
    from aiwen.plugins.executors.default import (
        DefaultAgentTemplate,
    )
    from aiwen.schemas.events.event_payloads import UserMessage

    logger.info("\n" + "=" * 80)
    logger.info("Example 5: Workspace Information Query")
    logger.info("=" * 80)

    # Get a workspace ID from database
    async with get_session("aiwen") as db:
        result = await db.execute(select(Workspace).limit(1))
        workspace = result.scalar_one_or_none()

        if not workspace:
            logger.warning("No workspace found in database, skipping example")
            return

        workspace_id = str(workspace.id)
        logger.info(f"Using workspace: {workspace.name} ({workspace_id})")

        config = {
            "model_provider": "tongyi",
            "model_name": "qwen-plus",
            "enable_browser_tools": False,
            "server_tools": ["utility"],  # Includes get_workspace_info, get_run_history
        }

        agent = DefaultAgentTemplate(config=config)
        logger.info("✓ Agent initialized with utility tools")

        # Test: Get workspace information
        message = UserMessage(
            message=f"Get detailed information about workspace {workspace_id} "
            f"and show me the recent run history."
        )

        result = await agent.run(message)
        logger.info(f"Agent response: {result['answer']}")


async def main():
    """Run all examples"""
    logger.info("=" * 80)
    logger.info("Agent with Server Tools - Configuration Examples")
    logger.info("=" * 80)

    # Example 1: All server tools
    await example_1_all_server_tools()

    # Example 2: Specific groups
    await example_2_specific_groups()

    # Example 3: Fine-grained control
    await example_3_fine_grained_control()

    # Example 4: Combined tools
    await example_4_combined_tools()

    # Example 5: Workspace query
    await example_5_workspace_query()

    logger.info("\n" + "=" * 80)
    logger.info("All examples completed!")
    logger.info("=" * 80)


async def quick_demo():
    """Quick demo showing the most common usage"""
    from aiwen.plugins.executors.default import (
        DefaultAgentTemplate,
    )
    from aiwen.schemas.events.event_payloads import UserMessage

    logger.info("\n" + "=" * 80)
    logger.info("Quick Demo: Most Common Configuration")
    logger.info("=" * 80)

    # Most common: Enable utility tools for time, workspace info, cache
    config = {
        "model_provider": "tongyi",
        "model_name": "qwen-plus",
        "enable_browser_tools": False,
        "server_tools": ["utility"],
    }

    agent = DefaultAgentTemplate(config=config)

    message = UserMessage(message="What's the current server time?")
    result = await agent.run(message)

    logger.info(f"Question: {message.message}")
    logger.info(f"Answer: {result['answer']}")


if __name__ == "__main__":
    # Run quick demo
    asyncio.run(quick_demo())

    # Uncomment to run all examples (requires database setup)
    # asyncio.run(main())
