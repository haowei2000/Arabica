#!/usr/bin/env python3
"""
Agent with User Tools Integration Example

Demonstrates the complete workflow:
1. Create user tools in database
2. Load tools dynamically
3. Initialize agent with user tools
4. Execute agent with custom tools
"""

import asyncio
import logging
from uuid import uuid4

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    """Complete integration example"""
    from structure.services.context.tools.dynamic_tool_loader import DynamicToolLoader

    from structure.extensions.database import get_session
    from structure.plugins.executors.default import (
        DefaultAgentTemplate,
    )
    from structure.schemas.context.tools.user_tool import UserToolCreate
    from structure.schemas.events.event_payloads import UserMessage
    from structure.services.context.tools.tool_crud import UserToolCRUD

    user_id = uuid4()

    logger.info("=" * 80)
    logger.info("Agent with User Tools - Complete Integration")
    logger.info("=" * 80)

    async with get_session("structure") as db:
        crud = UserToolCRUD(db)
        loader = DynamicToolLoader(db)

        # ===================================================================
        # Step 1: Create Custom Tools
        # ===================================================================
        logger.info("\n[Step 1] Creating custom tools...")

        # Calculator tool
        calculator_tool = UserToolCreate(
            name="calculator",
            display_name="Calculator",
            description="Performs basic arithmetic operations (add, subtract, multiply, divide)",
            execution_mode="server_run",
            category="math",
            tags=["math", "calculation"],
            timeout=10,
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "description": "Operation: add, subtract, multiply, divide",
                        "enum": ["add", "subtract", "multiply", "divide"],
                    },
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"},
                },
                "required": ["operation", "a", "b"],
            },
            code="""
operations = {
    'add': lambda a, b: a + b,
    'subtract': lambda a, b: a - b,
    'multiply': lambda a, b: a * b,
    'divide': lambda a, b: a / b if b != 0 else 'Division by zero'
}
result = {
    'value': operations[input_data['operation']](input_data['a'], input_data['b']),
    'operation': input_data['operation']
}
""",
        )

        tool1 = await crud.create_tool(user_id, calculator_tool)
        logger.info(f"✓ Created tool: {tool1.name} (id={tool1.id})")

        # Weather checker (simulated with fixed data)
        weather_tool = UserToolCreate(
            name="weather_check",
            display_name="Weather Checker",
            description="Get current weather information for a city (simulated data)",
            execution_mode="server_run",
            category="utility",
            tags=["weather", "information"],
            timeout=5,
            input_schema={
                "type": "object",
                "properties": {"city": {"type": "string", "description": "City name"}},
                "required": ["city"],
            },
            code="""
# Simulated weather data
weather_data = {
    'beijing': {'temp': 15, 'condition': 'Sunny', 'humidity': 45},
    'shanghai': {'temp': 22, 'condition': 'Cloudy', 'humidity': 60},
    'guangzhou': {'temp': 28, 'condition': 'Rainy', 'humidity': 80},
}

city = input_data['city'].lower()
weather = weather_data.get(city, {'temp': 20, 'condition': 'Unknown', 'humidity': 50})
result = {
    'city': input_data['city'],
    'temperature': weather['temp'],
    'condition': weather['condition'],
    'humidity': weather['humidity']
}
""",
        )

        tool2 = await crud.create_tool(user_id, weather_tool)
        logger.info(f"✓ Created tool: {tool2.name} (id={tool2.id})")

        # ===================================================================
        # Step 2: Load User Tools
        # ===================================================================
        logger.info("\n[Step 2] Loading tools into ToolRegistry...")

        loaded_tools = await loader.load_user_tools(user_id)
        logger.info(f"✓ Loaded {len(loaded_tools)} tools: {', '.join(loaded_tools)}")

        # ===================================================================
        # Step 3: Initialize Agent with User Tools
        # ===================================================================
        logger.info("\n[Step 3] Initializing agent with user tools...")

        agent_config = {
            "enable_browser_tools": False,  # Disable browser tools for this demo
            "approval_tools": [],
        }

        agent = DefaultAgentTemplate(config=agent_config)
        logger.info("✓ Agent initialized with user tools")

        # ===================================================================
        # Step 4: Test Agent with User Tools
        # ===================================================================
        logger.info("\n[Step 4] Testing agent with user tools...")

        # Test 1: Calculator
        logger.info("\n--- Test 1: Calculator ---")
        message1 = UserMessage(
            message="What is 125 multiplied by 8? Use the calculator tool."
        )

        result1 = await agent.run(message1)
        logger.info(f"Agent response: {result1['answer']}")

        # Test 2: Weather
        logger.info("\n--- Test 2: Weather Checker ---")
        message2 = UserMessage(
            message="What's the weather like in Beijing? Use the weather_check tool."
        )

        result2 = await agent.run(message2)
        logger.info(f"Agent response: {result2['answer']}")

        # Test 3: Multiple tools
        logger.info("\n--- Test 3: Multiple Tools ---")
        message3 = UserMessage(
            message="Calculate 50 + 30 and tell me the weather in Shanghai."
        )

        result3 = await agent.run(message3)
        logger.info(f"Agent response: {result3['answer']}")

        # ===================================================================
        # Step 5: Streaming Mode
        # ===================================================================
        logger.info("\n[Step 5] Testing streaming mode...")

        message4 = UserMessage(
            message="Calculate 100 divided by 4 and explain the result."
        )

        logger.info("\n--- Streaming events ---")
        async for event in agent.stream(message4):
            if event.event_type == "AGENT_TOKEN":
                print(event.data.get("token", ""), end="", flush=True)
            elif event.event_type == "TOOL_CALL":
                logger.info(
                    f"\n[TOOL] Calling: {event.data['tool_name']} with {event.data['arguments']}"
                )
            elif event.event_type == "TOOL_RESULT":
                logger.info(f"[TOOL] Result: {event.data['result']}")
            elif event.event_type == "AGENT_MESSAGE":
                print("\n")  # New line after final message

        # ===================================================================
        # Step 6: Cleanup
        # ===================================================================
        logger.info("\n[Step 6] Cleanup...")

        await crud.delete_tool(tool1.id, user_id)
        await crud.delete_tool(tool2.id, user_id)
        await loader.unload_tool(tool1.id)
        await loader.unload_tool(tool2.id)
        logger.info("✓ Deleted test tools")

    logger.info("\n" + "=" * 80)
    logger.info("Integration demo completed successfully!")
    logger.info("=" * 80)


async def demo_with_api():
    """Alternative: Create tools via API endpoints"""
    import httpx

    logger.info("\n" + "=" * 80)
    logger.info("Alternative: Using API Endpoints")
    logger.info("=" * 80)

    base_url = "http://localhost:8000"

    async with httpx.AsyncClient() as client:
        # Create tool
        logger.info("\n[1] Creating tool via API...")
        create_response = await client.post(
            f"{base_url}/tools/",
            json={
                "name": "text_counter",
                "display_name": "Text Counter",
                "description": "Count words and characters in text",
                "execution_mode": "server_run",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to analyze"}
                    },
                    "required": ["text"],
                },
                "code": "result = {'words': len(input_data['text'].split()), 'chars': len(input_data['text'])}",
            },
        )

        if create_response.status_code == 201:
            tool_data = create_response.json()
            logger.info(f"✓ Tool created: {tool_data['id']}")

            # Load tools
            logger.info("\n[2] Loading tools...")
            load_response = await client.post(f"{base_url}/tools/load")
            logger.info(f"✓ {load_response.json()['message']}")

            # Get tool schemas
            logger.info("\n[3] Getting tool schemas...")
            schemas_response = await client.get(f"{base_url}/tools/registry/schemas")
            schemas = schemas_response.json()
            logger.info(f"✓ Found {schemas['total']} tool schemas")

            # Execute tool
            logger.info("\n[4] Testing tool execution...")
            exec_response = await client.post(
                f"{base_url}/tools/execute",
                json={
                    "tool_id": tool_data["id"],
                    "parameters": {"text": "Hello world this is a test"},
                },
            )
            result = exec_response.json()
            logger.info(f"✓ Execution result: {result['data']}")

            logger.info("\n✓ API workflow completed")
        else:
            logger.error(f"Failed to create tool: {create_response.text}")


if __name__ == "__main__":
    # Run main integration demo
    asyncio.run(main())

    # Uncomment to run API demo
    # asyncio.run(demo_with_api())
