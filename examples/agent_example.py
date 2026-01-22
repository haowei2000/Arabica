# examples/agent_example.py
"""
Example script demonstrating how to use the agent system.

This script shows how to:
1. Create agents through the API
2. Run agents
3. Manage agents programmatically
"""

import asyncio
import httpx
from uuid import uuid4


async def demo_agent_system():
    """Demonstrate the agent system functionality."""
    base_url = "http://localhost:8000/api"

    # Create an NL2SQL agent
    nl2sql_agent_data = {
        "agent_code": f"nl2sql-demo-{uuid4().hex[:8]}",
        "agent_type": "NL2SQLAgent",
        "enabled": True,
        "config": {"model": "gpt-4.1", "temperature": 0.7},
        "version": 1,
    }

    # Create an Anomaly Detection agent
    anomaly_agent_data = {
        "agent_code": f"anomaly-demo-{uuid4().hex[:8]}",
        "agent_type": "AnomalyAgent",
        "enabled": True,
        "config": {"threshold": 0.9, "method": "isolation_forest"},
        "version": 1,
    }

    async with httpx.AsyncClient() as client:
        print("=== Agent System Demo ===\n")

        # 1. List available agent types
        print("1. Listing available agent types...")
        response = await client.get(f"{base_url}/agents/types")
        if response.status_code == 200:
            agent_types = response.json()
            print(f"Available agent types: {agent_types['agent_types']}\n")
        else:
            print(f"Failed to get agent types: {response.status_code}\n")

        # 2. Create agents
        print("2. Creating agents...")
        response = await client.post(f"{base_url}/agents/", json=nl2sql_agent_data)
        if response.status_code == 200:
            nl2sql_agent = response.json()
            nl2sql_agent_id = nl2sql_agent["agent_code"]  # Updated to agent_code
            print(f"Created NL2SQL agent: {nl2sql_agent_id}\n")
        else:
            print(f"Failed to create NL2SQL agent: {response.status_code}\n")
            return

        response = await client.post(f"{base_url}/agents/", json=anomaly_agent_data)
        if response.status_code == 200:
            anomaly_agent = response.json()
            print(
                f"Created Anomaly agent: {anomaly_agent['agent_code']}\n"
            )  # Updated to agent_code
        else:
            print(f"Failed to create Anomaly agent: {response.status_code}\n")
            return

        # 4. List all agents
        print("4. Listing all agents...")
        response = await client.get(f"{base_url}/agents/")
        if response.status_code == 200:
            agents = response.json()
            print(f"Found {len(agents)} agents:\n")
            for agent in agents:
                print(
                    f"  - {agent['agent_code']} ({agent['agent_type']})"
                )  # Updated to agent_code
            print()

        # 5. Get specific agent details
        print("5. Getting agent details...")
        response = await client.get(f"{base_url}/agents/{nl2sql_agent_id}")
        if response.status_code == 200:
            agent_details = response.json()
            print(f"Agent details: {agent_details}\n")
        else:
            print(f"Failed to get agent details: {response.status_code}\n")

        # 6. Run NL2SQL agent (this would normally work with a real implementation)
        print("6. Running NL2SQL agent...")
        run_payload = {"query": "Show me sales data for last month"}
        response = await client.post(
            f"{base_url}/agents/{nl2sql_agent_id}/run", json=run_payload
        )
        if response.status_code == 200:
            result = response.json()
            print(f"Agent result: {result}\n")
        else:
            print(f"Agent run failed: {response.status_code} - {response.text}\n")


if __name__ == "__main__":
    asyncio.run(demo_agent_system())
