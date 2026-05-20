from pydantic import ValidationError
import pytest

from structure.plugins.tools.context.read_context import ReadContextTool


def test_read_context_input_accepts_single_path():
    data = ReadContextTool.InputSchema(
        workspace_id="00000000-0000-0000-0000-000000000001",
        path="/skills/demo/content",
    )

    assert data.path == "/skills/demo/content"
    assert data.paths is None


def test_read_context_input_accepts_multiple_paths():
    data = ReadContextTool.InputSchema(
        workspace_id="00000000-0000-0000-0000-000000000001",
        paths=["/skills/demo/content", "/chat/uploads/demo.md"],
    )

    assert data.path is None
    assert data.paths == ["/skills/demo/content", "/chat/uploads/demo.md"]


def test_read_context_input_requires_path_or_paths():
    with pytest.raises(ValidationError):
        ReadContextTool.InputSchema(
            workspace_id="00000000-0000-0000-0000-000000000001"
        )
