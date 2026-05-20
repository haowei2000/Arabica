from structure.services.context.process import _join_path_prefix


def test_join_path_prefix_keeps_existing_prefixed_path():
    assert (
        _join_path_prefix("/memory/integration", "/memory/integration/fact")
        == "/memory/integration/fact"
    )


def test_join_path_prefix_deduplicates_overlapping_segment():
    assert (
        _join_path_prefix("/memory/integration", "/integration/fact")
        == "/memory/integration/fact"
    )


def test_join_path_prefix_adds_distinct_prefix():
    assert _join_path_prefix("/imported", "/memory/fact") == "/imported/memory/fact"
