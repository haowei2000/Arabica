from types import SimpleNamespace

from structure.plugins.structurers.file_context import FileContext, FileInput
from structure.plugins.structurers.knowledge_structure import KnowledgeStructurer
from structure.plugins.structurers.memory_structure import MemoryStructurer
from structure.plugins.structurers.skill_structure import SkillStructurer
from structure.plugins.structurers.stages import BuildChunksStage
from structure.plugins.structurers.tool import ToolStructurer


def test_skill_structurer_uses_hierarchical_skills_paths_for_text_content():
    skill = SimpleNamespace(
        name="Test Skill",
        description="Useful steps",
        files={},
        content="# Steps\nDo the thing.",
    )

    cores = SkillStructurer().structure(skill)
    by_path = {core.path: core for core in cores}

    assert set(by_path) == {
        "/skills/test_skill",
        "/skills/test_skill/content",
    }
    assert "# Steps" not in by_path["/skills/test_skill"].content
    assert by_path["/skills/test_skill/content"].content.startswith("# Steps")


def test_knowledge_structurer_uses_hierarchical_knowledge_paths():
    kb = SimpleNamespace(
        name="Ops KB",
        description="Runbook documents",
        documents=[],
    )

    cores = KnowledgeStructurer().structure(kb)

    assert [core.path for core in cores] == [
        "/knowledge/ops_kb",
        "/knowledge/ops_kb/base_ctx",
    ]


def test_file_chunk_builder_preserves_path_hierarchy():
    ctx = FileContext(
        file_input=FileInput(
            s3_key="skills/test/guide.md",
            file_name="Guide.md",
            base_path="/skills/test_skill",
            content_type="text/markdown",
        ),
        sections=[
            {
                "level": 1,
                "title": "Setup Steps",
                "content": "Install dependencies.",
                "position": 0,
            }
        ],
    )

    BuildChunksStage().process(ctx)

    assert [chunk.path for chunk in ctx.chunks] == [
        "/skills/test_skill/guidemd",
        "/skills/test_skill/guidemd/setup_steps",
    ]


def test_memory_structurer_preserves_memory_path_hierarchy():
    memory = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        path="memory/run-abc/summary",
        glance="Run summary",
        content="User asked about deployment.",
        scope="user",
        context_type="short_memory",
        importance=0,
        tags=["memory"],
    )

    cores = MemoryStructurer().structure(memory)

    assert cores[0].path == "/memory/run_abc/summary"


def test_tool_structurer_uses_tools_schema_path():
    tool = SimpleNamespace(
        name="Echo Tool",
        description="Echoes input",
        tool_type="inner",
        category="test",
        tags=["echo"],
        chain=None,
        inner_tool_name=None,
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    )

    cores = ToolStructurer().structure(tool)

    assert [core.path for core in cores] == [
        "/tools/echo_tool",
        "/tools/echo_tool/schema",
    ]
