"""Context framework for hierarchical path-based data organization.

This framework provides a generic path-addressable data structure with:
- Trie-based indexing for efficient queries
- Glob pattern matching (*, **)
- Progressive disclosure (glance/overview/detail)
- Schema nodes with aggregators

Example:
    from aiwen.frameworks.context import ContextStore, DetailLevel

    store = ContextStore("My Store")
    store.set("cluster/web-01",
        glance="Web-01 ✅ Running",
        overview={"cpu": "23%"},
        detail={"services": ["nginx", "redis"]}
    )

    # Query
    results = store.glob("cluster/**")
    tree = store.tree("cluster", DetailLevel.OVERVIEW)
"""

from aiwen.frameworks.context.layer import (
    ContextEntry,
    ContextStore,
    DetailLevel,
    QueryResult,
    SchemaNode,
    count_aggregator,
    normalize_path,
    parent_path,
    path_depth,
    path_segments,
)

__all__ = [
    "ContextEntry",
    "ContextStore",
    "DetailLevel",
    "QueryResult",
    "SchemaNode",
    "count_aggregator",
    "normalize_path",
    "parent_path",
    "path_depth",
    "path_segments",
]
