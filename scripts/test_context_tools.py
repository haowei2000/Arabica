"""Test script for context operation tools."""

import asyncio
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


async def test_context_tools():
    """Test all context operation tools."""
    from structure.extensions.database import get_session
    from structure.models.workspaces.workspace import Workspace
    from structure.plugins.tools.context import (
        CreateContextTool,
        DeleteContextTool,
        GlanceContextTool,
        GlobContextTool,
        ListContextTool,
        ReadContextTool,
        TreeContextTool,
        UpdateContextTool,
    )

    print("=" * 60)
    print("  Context Tools Test Suite")
    print("=" * 60)

    workspace_id = str(uuid4())

    async with get_session("structure") as session:
        # Create test workspace
        workspace = Workspace(
            id=workspace_id,
            name="Test Workspace",
            description="Test workspace for context tools",
            owner_id=uuid4(),
        )
        session.add(workspace)
        await session.commit()

        print(f"\n✓ Created test workspace: {workspace_id}\n")

        # 1. Test CreateContextTool
        print("1. Testing CreateContextTool")
        print("-" * 50)
        create_tool = CreateContextTool()

        result = await create_tool.execute(
            CreateContextTool.InputSchema(
                workspace_id=workspace_id,
                path=f"{workspace_id}/tools/web_search",
                glance="Web Search Tool — ✅ Ready",
                overview={"provider": "DuckDuckGo", "rate_limit": "100/hour"},
                detail={"description": "Search the web", "params": {"query": "string"}},
                tags=["tool", "search", "web"],
                content_type="application/json",
            )
        )
        print(f"  Status: {result.success}")
        print(f"  Message: {result.message}")
        assert result.success, "Create tool 1 failed"

        result = await create_tool.execute(
            CreateContextTool.InputSchema(
                workspace_id=workspace_id,
                path=f"{workspace_id}/tools/calculator",
                glance="Calculator — ✅ Ready",
                overview={"functions": ["add", "sub", "mul", "div"]},
                detail={"precision": "double", "max_value": 1e308},
                tags=["tool", "math"],
            )
        )
        print(f"  Created: {result.data.get('path')}")
        assert result.success, "Create tool 2 failed"

        result = await create_tool.execute(
            CreateContextTool.InputSchema(
                workspace_id=workspace_id,
                path=f"{workspace_id}/knowledge/python_guide",
                glance="Python Guide — Complete reference",
                overview="Python programming best practices and patterns",
                detail={"topics": ["basics", "OOP", "async", "testing"]},
                tags=["knowledge", "python", "programming"],
            )
        )
        assert result.success, "Create knowledge failed"
        print("  ✅ Created 3 contexts\n")

        # 2. Test ReadContextTool
        print("2. Testing ReadContextTool")
        print("-" * 50)
        read_tool = ReadContextTool()

        result = await read_tool.execute(
            ReadContextTool.InputSchema(
                workspace_id=workspace_id,
                path=f"{workspace_id}/tools/web_search",
                level="overview",
            )
        )
        print(f"  Status: {result.success}")
        print(f"  Glance: {result.data.get('context', {}).get('glance')}")
        assert result.success, "Read failed"
        print("  ✅ Read context successfully\n")

        # 3. Test GlanceContextTool
        print("3. Testing GlanceContextTool")
        print("-" * 50)
        glance_tool = GlanceContextTool()

        result = await glance_tool.execute(
            GlanceContextTool.InputSchema(
                workspace_id=workspace_id, prefix=f"{workspace_id}/tools"
            )
        )
        print(f"  Status: {result.success}")
        print(f"  Found: {result.data.get('count')} contexts")
        for glance in result.data.get("glances", []):
            print(f"    - {glance.get('glance')}")
        assert result.success, "Glance failed"
        print("  ✅ Glance scan successful\n")

        # 4. Test GlobContextTool
        print("4. Testing GlobContextTool")
        print("-" * 50)
        glob_tool = GlobContextTool()

        result = await glob_tool.execute(
            GlobContextTool.InputSchema(
                workspace_id=workspace_id,
                pattern=f"{workspace_id}/tools/**",
                level="glance",
            )
        )
        print(f"  Status: {result.success}")
        print(f"  Pattern: {result.data.get('pattern')}")
        print(f"  Matches: {result.data.get('count')}")
        for path in result.data.get("paths", []):
            print(f"    - {path}")
        assert result.success, "Glob failed"
        print("  ✅ Glob query successful\n")

        # 5. Test ListContextTool
        print("5. Testing ListContextTool")
        print("-" * 50)
        list_tool = ListContextTool()

        result = await list_tool.execute(
            ListContextTool.InputSchema(
                workspace_id=workspace_id,
                path=f"{workspace_id}/tools",
                mode="children",
                level="glance",
            )
        )
        print(f"  Status: {result.success}")
        print(f"  Mode: {result.data.get('mode')}")
        print(f"  Children: {result.data.get('count')}")
        assert result.success, "List failed"
        print("  ✅ List successful\n")

        # 6. Test TreeContextTool
        print("6. Testing TreeContextTool")
        print("-" * 50)
        tree_tool = TreeContextTool()

        result = await tree_tool.execute(
            TreeContextTool.InputSchema(
                workspace_id=workspace_id,
                root=f"{workspace_id}/tools",
                level="overview",
            )
        )
        print(f"  Status: {result.success}")
        print(f"  Total nodes: {result.data.get('total_nodes')}")
        assert result.success, "Tree failed"
        print("  ✅ Tree query successful\n")

        # 7. Test UpdateContextTool
        print("7. Testing UpdateContextTool")
        print("-" * 50)
        update_tool = UpdateContextTool()

        result = await update_tool.execute(
            UpdateContextTool.InputSchema(
                workspace_id=workspace_id,
                path=f"{workspace_id}/tools/calculator",
                glance="Calculator — ⚠️ Maintenance",
                tags=["tool", "math", "maintenance"],
            )
        )
        print(f"  Status: {result.success}")
        print(f"  Updated fields: {result.data.get('updated_fields')}")
        assert result.success, "Update failed"
        print("  ✅ Update successful\n")

        # 8. Test DeleteContextTool
        print("8. Testing DeleteContextTool")
        print("-" * 50)
        delete_tool = DeleteContextTool()

        # Try without confirm (should fail)
        result = await delete_tool.execute(
            DeleteContextTool.InputSchema(
                workspace_id=workspace_id,
                path=f"{workspace_id}/knowledge/python_guide",
                confirm=False,
            )
        )
        print(f"  Without confirm: {result.success} (expected False)")
        assert not result.success, "Delete should require confirmation"

        # Delete with confirm
        result = await delete_tool.execute(
            DeleteContextTool.InputSchema(
                workspace_id=workspace_id,
                path=f"{workspace_id}/knowledge/python_guide",
                confirm=True,
            )
        )
        print(f"  With confirm: {result.success}")
        print(f"  Deleted: {result.data.get('deleted_count')} context(s)")
        assert result.success, "Delete failed"
        print("  ✅ Delete successful\n")

        # Cleanup
        await delete_tool.execute(
            DeleteContextTool.InputSchema(
                workspace_id=workspace_id,
                path=workspace_id,
                recursive=True,
                confirm=True,
            )
        )
        await session.delete(workspace)
        await session.commit()

        print("=" * 60)
        print("  🎉 All Context Tools Tests Passed!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_context_tools())
