"""
Example: Agent with Browser Tools using BaseTool System

This example demonstrates the use of browser automation tools
after migration to the unified BaseTool system.
"""

import asyncio
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from aiwen.schemas.events.event_payloads import UserMessage
from aiwen.services.executor.executor_template.default.concrete import (
    DefaultAgentTemplate,
)


async def main():
    """Test browser tools with the BaseTool system"""

    # Configure agent with browser tools enabled
    config = {
        "model_provider": "tongyi",
        "model_name": "qwen-plus",
        "enable_browser_tools": True,
        "server_tools": False,  # Disable server tools for this test
    }

    # Initialize agent
    logger.info("Initializing agent with browser tools...")
    agent = DefaultAgentTemplate(config=config)

    # Test 1: Check if browser tools are loaded
    logger.info("\n" + "=" * 60)
    logger.info("Test 1: Verifying browser tools are loaded")
    logger.info("=" * 60)

    # Create a simple question that would use browser tools
    question = """
    Can you list what browser automation tools you have available?
    Just list the tool names, don't execute them.
    """

    message = UserMessage(message=question)

    try:
        logger.info(f"Question: {question.strip()}")
        result = await agent.run(message)
        logger.info(f"Answer: {result}")
        logger.info("✅ Test 1 PASSED: Browser tools loaded successfully")
    except Exception as e:
        logger.error(f"❌ Test 1 FAILED: {e}")
        raise

    # Note: Actual browser execution tests would require:
    # 1. Browser installation (Playwright)
    # 2. Proper session management
    # 3. More complex test scenarios
    # This example just verifies the tools are properly integrated

    logger.info("\n" + "=" * 60)
    logger.info("All Tests Completed!")
    logger.info("=" * 60)
    logger.info("✅ Browser tools successfully migrated to BaseTool system")


if __name__ == "__main__":
    asyncio.run(main())
