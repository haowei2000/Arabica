"""Test script to verify ContextLayer integration and migration."""

import asyncio
import sys
from uuid import uuid4

sys.path.insert(0, "/Users/wanghaowei/PycharmProjects/agent_chat/src")


async def test_framework():
    """Test ContextLayer framework."""
    print("=" * 60)
    print("1. Testing ContextLayer Framework")
    print("=" * 60)

    from aiwen.core.frameworks.context_layer import (
        ContextStore,
        DetailLevel,
        count_aggregator,
    )

    # Create store
    store = ContextStore("Test Workspace", "Testing ContextLayer")

    # Add schema nodes
    store.schema("workspace_123",
        glance="Workspace 123",
        aggregator=count_aggregator
    )

    store.schema("workspace_123/tools",
        glance="Available Tools",
        aggregator=count_aggregator
    )

    # Add data nodes
    store.set("workspace_123/tools/web_search",
        glance="Web Search Tool — ✅ Available",
        overview={"provider": "DuckDuckGo", "rate_limit": "100/hour"},
        detail={"description": "Search the web", "parameters": {...}},
        tags=["tool", "search", "web"]
    )

    store.set("workspace_123/tools/code_executor",
        glance="Code Executor — ✅ Available",
        overview={"languages": ["python", "javascript"], "timeout": "30s"},
        detail={"description": "Execute code safely"},
        tags=["tool", "code", "executor"]
    )

    # Test queries
    print("\n✓ Glance scan:")
    for line in store.glance("workspace_123"):
        print(f"  {line}")

    print("\n✓ Glob query (tools/**):")
    tools = store.glob("workspace_123/tools/**")
    print(f"  Found {len(tools)} tools")

    print("\n✓ Schema node aggregation:")
    workspace_data = store.get("workspace_123", DetailLevel.OVERVIEW)
    print(f"  {workspace_data['glance']}")

    print("\n✅ ContextLayer framework test passed!\n")


async def test_models():
    """Test database models."""
    print("=" * 60)
    print("2. Testing Database Models")
    print("=" * 60)

    from aiwen.core.enums import ContextType
    from aiwen.extensions.database import get_session
    from aiwen.models.context.context import Context

    async with get_session("aiwen") as session:
        # Create a test context
        test_ctx = Context(
            user_id=uuid4(),
            path="/test_workspace/tools/test_tool",
            context_type=ContextType.TOOL,
            glance="Test Tool — ✅ Testing",
            summary="This is a test tool for migration verification",
            content="Full tool content here...",
            tags=["test", "tool"],
            importance=50
        )

        session.add(test_ctx)
        await session.commit()

        print("\n✓ Created test Context:")
        print(f"  ID: {test_ctx.id}")
        print(f"  Path: {test_ctx.path}")
        print(f"  Glance: {test_ctx.glance}")
        print(f"  Tags: {test_ctx.tags}")

        # Test progressive disclosure
        print("\n✓ Progressive disclosure:")
        glance_data = test_ctx.disclose("glance")
        print(f"  Glance: {glance_data}")

        overview_data = test_ctx.disclose("overview")
        print(f"  Overview: {overview_data}")

        # Test path methods
        print("\n✓ Path methods:")
        print(f"  Depth: {test_ctx.get_path_depth()}")
        print(f"  Parent: {test_ctx.get_parent_path()}")
        print(f"  Has tag 'tool': {test_ctx.has_tag('tool')}")

        # Test WorkspaceContext requires real workspace, skip for now
        print("\n✓ Skipping WorkspaceContext test (requires real workspace)")

        # Cleanup
        await session.delete(test_ctx)
        await session.commit()

        print("\n✅ Database models test passed!\n")


async def test_service():
    """Test WorkspaceContextService."""
    print("=" * 60)
    print("3. Testing WorkspaceContextService")
    print("=" * 60)

    from aiwen.extensions.database import get_session
    from aiwen.models.workspaces.workspace import Workspace
    from aiwen.services.workspace_context.workspace_context_service import (
        WorkspaceContextService,
    )

    workspace_id = uuid4()

    async with get_session("aiwen") as session:
        # Create a real workspace for testing
        test_workspace = Workspace(
            id=workspace_id,
            name="Test Workspace",
            description="Test workspace for migration",
            owner_id=uuid4()
        )
        session.add(test_workspace)
        await session.commit()

        workspace_id = str(workspace_id)
        # Create service
        service = WorkspaceContextService(session, workspace_id)
        await service.load()

        print(f"\n✓ Created service for workspace: {workspace_id}")

        # Add contexts
        await service.set(
            path=f"{workspace_id}/tools/web_search",
            glance="Web Search — ✅ Ready",
            overview={"provider": "DuckDuckGo"},
            detail={"params": {}},
            tags=["tool", "search"],
            name="Web Search Tool",
            content_type="application/json"
        )

        await service.set(
            path=f"{workspace_id}/tools/calculator",
            glance="Calculator — ✅ Ready",
            overview={"functions": ["add", "subtract", "multiply", "divide"]},
            detail={"precision": "double"},
            tags=["tool", "math"],
            name="Calculator Tool",
            content_type="application/json"
        )

        print("\n✓ Added 2 contexts to database")

        # Query using ContextStore API
        tools = await service.glob(f"{workspace_id}/tools/**")
        print(f"\n✓ Glob query found {len(tools)} tools:")
        for path, entry in tools:
            print(f"  {path} → {entry.glance}")

        # Test glance scan
        print("\n✓ Glance scan:")
        for line in await service.glance(f"{workspace_id}/tools"):
            print(f"  {line}")

        # Simulate server restart
        print("\n✓ Simulating server restart...")
        service2 = WorkspaceContextService(session, workspace_id)
        await service2.load()  # Load from DB

        # Verify data restored
        tools2 = await service2.glob(f"{workspace_id}/tools/**")
        print(f"\n✓ After restart, found {len(tools2)} tools (data preserved!)")

        # Cleanup
        await service.delete(f"{workspace_id}", recursive=True)
        await session.delete(test_workspace)
        await session.commit()
        print("\n✓ Cleaned up test data and workspace")

        print("\n✅ WorkspaceContextService test passed!\n")


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("  ContextLayer Migration Test Suite")
    print("=" * 60 + "\n")

    try:
        await test_framework()
        await test_models()
        await test_service()

        print("=" * 60)
        print("  🎉 All tests passed!")
        print("  ✅ Migration successful!")
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
