from structure.services.events.handlers.tool_handler import _inject_runtime_arguments


def test_inject_runtime_arguments_updates_mcp_arguments_wrapper():
    arguments = {"arguments": {"path": "/chat/uploads/file.md"}}

    injected = _inject_runtime_arguments(
        arguments,
        workspace_id="workspace-1",
        run_id="run-1",
    )

    assert injected["workspace_id"] == "workspace-1"
    assert injected["run_id"] == "run-1"
    assert injected["arguments"]["workspace_id"] == "workspace-1"
    assert injected["arguments"]["run_id"] == "run-1"
    assert injected["arguments"]["path"] == "/chat/uploads/file.md"
    assert "workspace_id" not in arguments["arguments"]


def test_inject_runtime_arguments_preserves_existing_runtime_ids():
    arguments = {
        "workspace_id": "outer-workspace",
        "arguments": {
            "workspace_id": "inner-workspace",
            "run_id": "inner-run",
            "path": "/skills/demo/content",
        },
    }

    injected = _inject_runtime_arguments(
        arguments,
        workspace_id="workspace-1",
        run_id="run-1",
    )

    assert injected["workspace_id"] == "outer-workspace"
    assert injected["run_id"] == "run-1"
    assert injected["arguments"]["workspace_id"] == "inner-workspace"
    assert injected["arguments"]["run_id"] == "inner-run"
