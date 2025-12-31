# examples/streaming_chat_example.py
"""
Example script demonstrating how to use the streaming chat functionality.

This script shows how to:
1. Create agents through the API
2. Chat with agents using streaming SSE
3. Process streamed responses in real-time
"""

import asyncio
import httpx
import json
from uuid import uuid4


async def demo_streaming_chat():
    """Demonstrate the streaming chat functionality."""
    base_url = "http://localhost:8000/api"

    # Create an NL2SQL agent
    agent_data = {
        "agent_code": f"nl2sql-stream-demo-{uuid4().hex[:8]}",
        "agent_type": "NL2SQLAgent",
        "enabled": True,
        "config": {
            "model": "gpt-4.1",
            "temperature": 0.7
        },
        "version": 1
    }

    async with httpx.AsyncClient() as client:
        print("=== Streaming Chat Demo ===\n")

        # 1. Create agent
        print("1. Creating NL2SQL agent...")
        response = await client.post(
            f"{base_url}/agents/",
            json=agent_data
        )
        if response.status_code == 200:
            created_agent = response.json()
            agent_code = created_agent["agent_code"]
            print(f"Created agent: {agent_code}\n")
        else:
            print(f"Failed to create agent: {response.status_code}\n")
            return

        # 2. Chat with agent using streaming (direct endpoint)
        print("2. Chatting with agent using direct streaming...")
        chat_payload = {
            "query": "Show me sales data for last month"
        }

        async with client.stream(
            "POST",
            f"{base_url}/chat/{agent_code}/direct",
            json=chat_payload
        ) as response:
            if response.status_code == 200:
                print("Streaming response:")
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        # Parse SSE data
                        data = line[6:].strip()  # Remove "data: " prefix
                        try:
                            event_data = json.loads(data)
                            event_type = event_data.get("event", "unknown")
                            event_content = event_data.get("data", "")

                            if event_type == "status":
                                print(f"  [STATUS] {event_content}")
                            elif event_type == "chunk":
                                print(f"  [CHUNK] {event_content.strip()}")
                            elif event_type == "success":
                                print(f"  [SUCCESS] {event_content}")
                            elif event_type == "error":
                                print(f"  [ERROR] {event_content}")
                        except json.JSONDecodeError:
                            print(f"  [DATA] {data}")
                print()
            else:
                print(f"Failed to start chat: {response.status_code}\n")

        # 3. Chat with agent using queued streaming
        print("3. Chatting with agent using queued streaming...")
        chat_payload = {
            "query": "Find customers with high spending patterns"
        }

        async with client.stream(
            "POST",
            f"{base_url}/chat/{agent_code}",
            json=chat_payload
        ) as response:
            if response.status_code == 200:
                print("Queued streaming response:")
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        # Parse SSE data
                        data = line[6:].strip()  # Remove "data: " prefix
                        try:
                            event_data = json.loads(data)
                            event_type = event_data.get("event", "unknown")
                            event_content = event_data.get("data", "")

                            if event_type == "status":
                                print(f"  [STATUS] {event_content}")
                            elif event_type == "chunk":
                                print(f"  [CHUNK] {event_content.strip()}")
                            elif event_type == "success":
                                print(f"  [SUCCESS] {event_content}")
                            elif event_type == "error":
                                print(f"  [ERROR] {event_content}")
                        except json.JSONDecodeError:
                            print(f"  [DATA] {data}")
                print()
            else:
                print(f"Failed to start queued chat: {response.status_code}\n")

        print("=== Demo Complete ===")


if __name__ == "__main__":
    asyncio.run(demo_streaming_chat())