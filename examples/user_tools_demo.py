#!/usr/bin/env python3
"""
User Tools Demo

Complete demonstration of creating, loading, and using custom tools in agents.
"""

import asyncio
import json
import logging
from uuid import UUID, uuid4

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def demo():
    """Run complete user tools demo"""
    from aiwen.extensions.database import get_session
    from aiwen.schemas.tools.user_tool import UserToolCreate
    from aiwen.services.tools.tool_registry import ToolRegistry
    from aiwen.services.tools.dynamic_tool_loader import DynamicToolLoader
    from aiwen.services.tools.user_tool_crud import UserToolCRUD

    # Simulated user ID
    user_id = uuid4()

    logger.info("=" * 80)
    logger.info("User Tools Demo - Complete Workflow")
    logger.info("=" * 80)

    async with get_session("aiwen") as db:
        crud = UserToolCRUD(db)
        loader = DynamicToolLoader(db)

        # ===================================================================
        # Step 1: Create Custom Tools
        # ===================================================================
        logger.info("\n[Step 1] Creating custom tools...")

        # Tool 1: Calculator
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

        # Tool 2: Text Analyzer
        text_analyzer_tool = UserToolCreate(
            name="text_analyzer",
            display_name="Text Analyzer",
            description="Analyzes text and returns statistics (word count, character count, etc.)",
            execution_mode="server_run",
            category="text",
            tags=["text", "analysis"],
            timeout=10,
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to analyze"}
                },
                "required": ["text"],
            },
            code="""
text = input_data['text']
words = text.split()
result = {
    'word_count': len(words),
    'char_count': len(text),
    'line_count': len(text.splitlines()),
    'unique_words': len(set(words)),
    'average_word_length': sum(len(word) for word in words) / len(words) if words else 0
}
""",
        )

        tool2 = await crud.create_tool(user_id, text_analyzer_tool)
        logger.info(f"✓ Created tool: {tool2.name} (id={tool2.id})")

        # ===================================================================
        # Step 2: List Created Tools
        # ===================================================================
        logger.info("\n[Step 2] Listing user tools...")

        tools = await crud.list_user_tools(user_id)
        logger.info(f"Total tools: {len(tools)}")
        for tool in tools:
            logger.info(f"  - {tool.name}: {tool.display_name} ({tool.category})")

        # ===================================================================
        # Step 3: Load Tools into Agent
        # ===================================================================
        logger.info("\n[Step 3] Loading tools into agent...")

        loaded_tools = await loader.load_user_tools(user_id)
        logger.info(f"✓ Loaded {len(loaded_tools)} tools: {', '.join(loaded_tools)}")

        # ===================================================================
        # Step 4: Get Tool Schemas for LLM
        # ===================================================================
        logger.info("\n[Step 4] Getting tool schemas for LLM...")

        schemas = ToolRegistry.get_all_schemas(format="openai")
        logger.info(f"Generated {len(schemas)} tool schemas")

        for schema in schemas:
            if schema["function"]["name"] in loaded_tools:
                logger.info(f"\nSchema for {schema['function']['name']}:")
                logger.info(json.dumps(schema, indent=2))

        # ===================================================================
        # Step 5: Execute Tools Directly
        # ===================================================================
        logger.info("\n[Step 5] Testing tool execution...")

        # Test calculator
        calc_tool = ToolRegistry.get_tool_instance("calculator")
        if calc_tool:
            result1 = await calc_tool(operation="add", a=15, b=27)
            logger.info(f"Calculator result (15 + 27): {result1}")

            result2 = await calc_tool(operation="multiply", a=7, b=8)
            logger.info(f"Calculator result (7 * 8): {result2}")

        # Test text analyzer
        analyzer_tool = ToolRegistry.get_tool_instance("text_analyzer")
        if analyzer_tool:
            result3 = await analyzer_tool(
                text="Hello world! This is a test sentence. Testing text analysis."
            )
            logger.info(f"Text analyzer result: {result3}")

        # ===================================================================
        # Step 6: Simulate Agent Usage
        # ===================================================================
        logger.info("\n[Step 6] Simulating agent usage...")

        # Simulate LLM response with tool call
        simulated_llm_response = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": '{"operation": "add", "a": 42, "b": 58}',
                    },
                }
            ]
        }

        logger.info("LLM requested tool call:")
        logger.info(json.dumps(simulated_llm_response, indent=2))

        # Execute tool call
        for tool_call in simulated_llm_response["tool_calls"]:
            tool_name = tool_call["function"]["name"]
            tool_args = json.loads(tool_call["function"]["arguments"])

            logger.info(f"\nExecuting tool: {tool_name}")
            logger.info(f"Arguments: {tool_args}")

            tool_instance = ToolRegistry.get_tool_instance(tool_name)
            if tool_instance:
                result = await tool_instance(**tool_args)
                logger.info(f"Result: {result}")

        # ===================================================================
        # Step 7: Tool Management
        # ===================================================================
        logger.info("\n[Step 7] Tool management operations...")

        # Update tool
        from aiwen.schemas.tools.user_tool import UserToolUpdate

        updated = await crud.update_tool(
            tool1.id,
            user_id,
            UserToolUpdate(description="Updated: Enhanced calculator with more operations"),
        )
        if updated:
            logger.info(f"✓ Updated tool: {updated.name}")

        # Disable tool
        disabled = await crud.toggle_enabled(tool2.id, user_id, False)
        if disabled:
            logger.info(f"✓ Disabled tool: {disabled.name}")

        # List enabled tools only
        enabled_tools = await crud.list_user_tools(user_id, enabled_only=True)
        logger.info(f"Enabled tools: {[t.name for t in enabled_tools]}")

        # ===================================================================
        # Step 8: Cleanup
        # ===================================================================
        logger.info("\n[Step 8] Cleanup...")

        # Delete tools
        await crud.delete_tool(tool1.id, user_id)
        await crud.delete_tool(tool2.id, user_id)
        logger.info("✓ Deleted test tools")

        # Unload from registry
        await loader.unload_tool(tool1.id)
        await loader.unload_tool(tool2.id)
        logger.info("✓ Unloaded tools from registry")

    logger.info("\n" + "=" * 80)
    logger.info("Demo completed successfully!")
    logger.info("=" * 80)


async def demo_with_langchain():
    """Demo integration with LangChain"""
    from aiwen.extensions.database import get_session
    from aiwen.services.tools.tool_registry import ToolRegistry
    from aiwen.services.tools.dynamic_tool_loader import DynamicToolLoader

    logger.info("\n" + "=" * 80)
    logger.info("LangChain Integration Demo")
    logger.info("=" * 80)

    user_id = uuid4()

    async with get_session("aiwen") as db:
        loader = DynamicToolLoader(db)

        # Load tools
        await loader.load_user_tools(user_id)

        # Convert to LangChain format
        logger.info("\nConverting tools to LangChain format...")

        langchain_schemas = ToolRegistry.get_all_schemas(format="langchain")

        for schema in langchain_schemas:
            logger.info(f"\nLangChain schema for {schema['name']}:")
            logger.info(f"  Name: {schema['name']}")
            logger.info(f"  Description: {schema['description']}")
            logger.info(f"  Args Schema: {schema['args_schema']}")

        logger.info("\n✓ Tools ready for LangChain agent")


if __name__ == "__main__":
    # Run main demo
    asyncio.run(demo())

    # Uncomment to run LangChain demo
    # asyncio.run(demo_with_langchain())
