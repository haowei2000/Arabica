# aiwen/services/agents/concrete.py
import asyncio
from collections.abc import AsyncGenerator
from typing import Any, Dict, List

from aiwen.services.agents.agent_registry import register_agent
from aiwen.services.agents.base import BaseAgentTemplate


@register_agent
class NL2SQLAgentTemplate(BaseAgentTemplate):
    """Natural Language to SQL conversion agent."""

    TEMPLATE = {
        "template_code": "NL2SQL001",
        "template_name": "Natural Language to SQL Agent",
        "enabled": True,
        "version": 1,
        "config": {}
    }

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """
        Execute the NL2SQL workflow.

        Args:
            input_data: Contains 'query' key with natural language query

        Returns:
            Dictionary with intent, generated SQL, and executed data
        """
        query = input_data["query"]

        intent = await self.parse_intent(query)
        sql = await self.generate_sql(intent)
        data = await self.execute_sql(sql)

        return {
            "intent": intent,
            "sql": sql,
            "data": data
        }

    async def stream(self, input_data: dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        Stream the NL2SQL agent's output as it is generated.

        Args:
            input_data: Contains 'query' key with natural language query

        Yields:
            Chunks of the agent's thinking process and final result
        """
        query = input_data["query"]

        # Simulate thinking process
        yield "Analyzing your query...\n"
        await asyncio.sleep(0.1)

        yield "Parsing intent...\n"
        intent = await self.parse_intent(query)
        await asyncio.sleep(0.1)

        yield f"Identified intent: {intent.get('original_query', query)}\n"
        await asyncio.sleep(0.1)

        yield "Generating SQL...\n"
        sql = await self.generate_sql(intent)
        await asyncio.sleep(0.1)

        yield f"Generated SQL: {sql}\n"
        await asyncio.sleep(0.1)

        yield "Executing query...\n"
        data = await self.execute_sql(sql)
        await asyncio.sleep(0.1)

        yield f"Query executed successfully. Found {len(data)} rows.\n"
        await asyncio.sleep(0.1)

        # Final result
        result = {
            "intent": intent,
            "sql": sql,
            "data": data
        }
        yield f"Final result: {result}\n"

    async def parse_intent(self, query: str) -> dict[str, Any]:
        """
        Parse the natural language query to extract intent.

        Args:
            query: Natural language query string

        Returns:
            Dictionary containing parsed intent
        """
        # Implementation would go here
        # This is a placeholder implementation
        return {
            "original_query": query,
            "parsed_elements": {}
        }

    async def generate_sql(self, intent: dict[str, Any]) -> str:
        """
        Generate SQL based on the parsed intent.

        Args:
            intent: Parsed intent from parse_intent method

        Returns:
            Generated SQL string
        """
        # Implementation would go here
        # This is a placeholder implementation
        return "SELECT * FROM table LIMIT 10;"

    async def execute_sql(self, sql: str) -> list[dict[str, Any]]:
        """
        Execute the generated SQL and return results.

        Args:
            sql: SQL string to execute

        Returns:
            List of dictionaries representing query results
        """
        # Implementation would go here
        # This is a placeholder implementation
        return [{"placeholder": "result"}]
