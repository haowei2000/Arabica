#!/usr/bin/env python3
"""
Quick verification script for the protocol layer.

This script performs basic sanity checks to ensure the protocol layer
is correctly implemented and working.
"""

import sys
from typing import ClassVar


def verify_protocol_imports():
    """Verify that protocols can be imported."""
    print("1. Verifying protocol imports...")
    try:
        from structure.core.interfaces import (
            PROTOCOL_REGISTRY,
            ExecutorProtocol,
            RegistrableProtocol,
            RegistryProtocol,
            ToolProtocol,
            is_executor,
            is_registry,
            is_tool,
        )

        print("   ✓ All protocols imported successfully")
        return True
    except ImportError as e:
        print(f"   ✗ Failed to import protocols: {e}")
        return False


def verify_protocol_metadata():
    """Verify that protocol metadata is available."""
    print("\n2. Verifying protocol metadata...")
    try:
        from structure.core.interfaces import PROTOCOL_REGISTRY

        expected_protocols = [
            "RegistrableProtocol",
            "ToolProtocol",
            "ExecutorProtocol",
            "RegistryProtocol",
        ]

        for protocol_name in expected_protocols:
            if protocol_name not in PROTOCOL_REGISTRY:
                print(f"   ✗ Missing protocol: {protocol_name}")
                return False
            print(f"   ✓ {protocol_name} metadata exists")

        return True
    except Exception as e:
        print(f"   ✗ Failed to verify metadata: {e}")
        return False


def verify_tool_protocol():
    """Verify that BaseTool implements ToolProtocol."""
    print("\n3. Verifying BaseTool implements ToolProtocol...")
    try:
        from structure.core.interfaces import ToolProtocol, is_tool
        from structure.core.interfaces.tool import (
            BaseTool,
            ToolMetadata,
            ToolOutputSchema,
        )

        class TestTool(BaseTool):
            METADATA: ClassVar[ToolMetadata] = ToolMetadata(
                name="test_tool",
                display_name="Test Tool",
                description="A test tool",
            )

            async def execute(self, _input_data):
                return ToolOutputSchema(success=True, message="Test successful")

        tool = TestTool()

        # Check with isinstance
        if not isinstance(tool, ToolProtocol):
            print("   ✗ TestTool does not satisfy ToolProtocol (isinstance)")
            return False
        print("   ✓ TestTool satisfies ToolProtocol (isinstance)")

        # Check with is_tool helper
        if not is_tool(tool):
            print("   ✗ TestTool does not satisfy ToolProtocol (is_tool)")
            return False
        print("   ✓ TestTool satisfies ToolProtocol (is_tool)")

        # Check required methods
        required_methods = [
            "execute",
            "validate_input",
            "format_output",
            "get_json_schema",
            "get_metadata",
        ]
        for method_name in required_methods:
            if not callable(getattr(tool, method_name, None)):
                print(f"   ✗ Missing or non-callable method: {method_name}")
                return False
        print(f"   ✓ All required methods present: {', '.join(required_methods)}")

        return True
    except Exception as e:
        print(f"   ✗ Failed to verify ToolProtocol: {e}")
        import traceback

        traceback.print_exc()
        return False


def verify_registry_protocol():
    """Verify that registries implement RegistryProtocol."""
    print("\n4. Verifying registries implement RegistryProtocol...")
    try:
        from structure.core.interfaces import RegistryProtocol, is_registry
        from structure.registries.core import ExecutorRegistry, ToolRegistry

        # Test ToolRegistry
        tool_registry = ToolRegistry()
        if not isinstance(tool_registry, RegistryProtocol):
            print("   ✗ ToolRegistry does not satisfy RegistryProtocol (isinstance)")
            return False
        print("   ✓ ToolRegistry satisfies RegistryProtocol (isinstance)")

        if not is_registry(tool_registry):
            print("   ✗ ToolRegistry does not satisfy RegistryProtocol (is_registry)")
            return False
        print("   ✓ ToolRegistry satisfies RegistryProtocol (is_registry)")

        # Test ExecutorRegistry
        executor_registry = ExecutorRegistry()
        if not isinstance(executor_registry, RegistryProtocol):
            print(
                "   ✗ ExecutorRegistry does not satisfy RegistryProtocol (isinstance)"
            )
            return False
        print("   ✓ ExecutorRegistry satisfies RegistryProtocol (isinstance)")

        return True
    except Exception as e:
        print(f"   ✗ Failed to verify RegistryProtocol: {e}")
        import traceback

        traceback.print_exc()
        return False


def verify_type_checkers():
    """Verify that type checker functions work."""
    print("\n5. Verifying type checker functions...")
    try:
        from structure.core.interfaces import is_executor, is_registry, is_tool
        from structure.registries.core import ToolRegistry

        # Test with valid objects
        registry = ToolRegistry()
        assert is_registry(registry), "is_registry failed for ToolRegistry"
        print("   ✓ is_registry works correctly")

        # Test with invalid objects
        class NotAComponent:
            pass

        obj = NotAComponent()
        assert not is_tool(obj), "is_tool incorrectly identified non-tool"
        assert not is_executor(obj), "is_executor incorrectly identified non-executor"
        assert not is_registry(obj), "is_registry incorrectly identified non-registry"
        print("   ✓ Type checkers correctly reject invalid objects")

        return True
    except Exception as e:
        print(f"   ✗ Failed to verify type checkers: {e}")
        import traceback

        traceback.print_exc()
        return False


def verify_direct_imports():
    """Verify that all source modules are directly importable."""
    print("\n6. Verifying direct source module imports...")
    try:
        from structure.core.interfaces import (
            AgentEvent,
            Executor,
            ExecutorProtocol,
            RegistryProtocol,
            ToolProtocol,
            WaitingForTool,
        )
        from structure.core.interfaces.tool import BaseTool, InnerTool, ToolMetadata
        from structure.registries.core import (
            ExecutorRegistry,
            ToolRegistry,
            register_executor,
            register_tool,
        )
        from structure.registries.manager import RegistryManager, get_registry

        print("   ✓ All source modules importable directly")
        return True
    except ImportError as e:
        print(f"   ✗ Failed to import from source modules: {e}")
        return False


def main():
    """Run all verification checks."""
    print("=" * 70)
    print("Protocol Layer Verification")
    print("=" * 70)

    checks = [
        verify_protocol_imports,
        verify_protocol_metadata,
        verify_tool_protocol,
        verify_registry_protocol,
        verify_type_checkers,
        verify_direct_imports,
    ]

    results = []
    for check in checks:
        try:
            result = check()
            results.append(result)
        except Exception as e:
            print(f"\n✗ Unexpected error in {check.__name__}: {e}")
            import traceback

            traceback.print_exc()
            results.append(False)

    print("\n" + "=" * 70)
    print("Verification Summary")
    print("=" * 70)

    passed = sum(results)
    total = len(results)
    success_rate = (passed / total) * 100 if total > 0 else 0

    print(f"Passed: {passed}/{total} ({success_rate:.1f}%)")

    if all(results):
        print("\n✓ All verifications passed!")
        print("\nThe protocol layer is correctly implemented and working.")
        return 0
    print("\n✗ Some verifications failed!")
    print("\nPlease review the errors above and fix the issues.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
