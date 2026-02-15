"""
ContextLayer - 基于路径寻址的上下文结构化与渐进式披露框架

核心变化（相比树形对象模型）：
  - 节点通过路径字符串定位，如 "cluster/web-01/nginx"
  - 扁平化存储 + Trie 索引，支持高效前缀查询
  - 支持 glob 通配符：* 匹配单层，** 匹配任意深度
  - 生产者只需知道路径约定即可写入，松耦合设计

快速上手：
    from aiwen.frameworks.context_layer import ContextStore, DetailLevel

    ctx = ContextStore("生产环境监控")

    ctx.set("cluster/web-01",
        glance="Web-01 — ✅ 运行正常",
        overview={"状态": "健康", "CPU": "23%"},
        detail={"ip": "10.0.1.15", "services": ["nginx", "redis"]},
        tags=["production", "web"],
    )

    # 按层级获取
    print(ctx.get("cluster/web-01", DetailLevel.GLANCE))

    # 通配查询
    ctx.glob("cluster/*/nginx")        # 所有服务器的 nginx
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union


# ─────────────────────────────────────────────
# 1. 层级枚举
# ─────────────────────────────────────────────

class DetailLevel(IntEnum):
    """信息披露层级，数值越大信息越详细。"""
    GLANCE   = 1   # 扫描层：一句话 / 一个状态
    OVERVIEW = 2   # 概览层：结构化摘要
    DETAIL   = 3   # 详情层：完整数据

    @classmethod
    def from_str(cls, s: str) -> "DetailLevel":
        return {"glance": cls.GLANCE, "overview": cls.OVERVIEW, "detail": cls.DETAIL}[s.lower()]


# ─────────────────────────────────────────────
# 2. 路径工具函数
# ─────────────────────────────────────────────

def normalize_path(path: str) -> str:
    """规范化路径：去除首尾斜杠、合并连续斜杠。"""
    return re.sub(r"/+", "/", path.strip("/"))


def parent_path(path: str) -> Optional[str]:
    """返回父路径，根节点返回 None。"""
    parts = normalize_path(path).rsplit("/", 1)
    return parts[0] if len(parts) > 1 else None


def path_depth(path: str) -> int:
    """路径深度（段数）。"""
    return len(normalize_path(path).split("/"))


def path_segments(path: str) -> List[str]:
    """拆分路径为段列表。"""
    return normalize_path(path).split("/")


# ─────────────────────────────────────────────
# 3. 节点数据（纯数据容器）
# ─────────────────────────────────────────────

@dataclass
class ContextEntry:
    """
    单个上下文条目，承载三层数据。

    与树形版的 ContextNode 不同：
    - 不持有 children 引用（由 Store 管理拓扑）
    - 不持有 key（路径即标识，由 Store 维护映射）
    """
    glance: str
    overview: Union[Dict[str, Any], str, None] = None
    detail: Any = None
    tags: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if "created_at" not in self.meta:
            self.meta["created_at"] = datetime.now().isoformat()

    def disclose(self, level: DetailLevel = DetailLevel.OVERVIEW) -> Dict[str, Any]:
        """按层级返回数据，每层包含上层内容，保证自足性。"""
        result: Dict[str, Any] = {"glance": self.glance}
        if level >= DetailLevel.OVERVIEW and self.overview is not None:
            result["overview"] = self.overview
        if level >= DetailLevel.DETAIL:
            if self.detail is not None:
                result["detail"] = self.detail
            result["meta"] = self.meta
            result["tags"] = self.tags
        return result

    def __str__(self) -> str:
        return self.glance


# ─────────────────────────────────────────────
# 3.5 骨架节点（SchemaNode）
# ─────────────────────────────────────────────

@dataclass
class SchemaNode:
    """骨架节点 — 只定义结构角色，不承载业务数据。"""
    glance: Optional[str] = None
    overview: Union[Dict[str, Any], str, None] = None
    tags: List[str] = field(default_factory=list)
    aggregator: Optional[Callable[[List[Tuple[str, "ContextEntry"]]], Dict[str, Any]]] = None

    def resolve(self, children: List[Tuple[str, "ContextEntry"]]) -> ContextEntry:
        """将骨架节点解析为 ContextEntry。"""
        glance = self.glance or ""
        overview = self.overview
        detail = None

        if self.aggregator and children:
            agg = self.aggregator(children)
            if "glance" in agg:
                glance = agg["glance"] if not self.glance else self.glance
            if "overview" in agg:
                overview = agg["overview"]
            if "detail" in agg:
                detail = agg["detail"]

        return ContextEntry(
            glance=glance,
            overview=overview,
            detail=detail,
            tags=self.tags,
            meta={"type": "schema", "created_at": datetime.now().isoformat()},
        )


# ─── 内置聚合器 ───

def count_aggregator(children: List[Tuple[str, ContextEntry]]) -> Dict[str, Any]:
    """最简单的聚合：统计子节点数量和状态。"""
    total = len(children)
    status_counts: Dict[str, int] = {}
    for _, entry in children:
        g = entry.glance
        if "✅" in g:
            status_counts["healthy"] = status_counts.get("healthy", 0) + 1
        elif "⚠️" in g:
            status_counts["warning"] = status_counts.get("warning", 0) + 1
        elif "❌" in g:
            status_counts["error"] = status_counts.get("error", 0) + 1

    healthy = status_counts.get("healthy", 0)
    warning = status_counts.get("warning", 0)
    error = status_counts.get("error", 0)

    if error > 0:
        status_icon = "❌"
    elif warning > 0:
        status_icon = "⚠️"
    else:
        status_icon = "✅"

    return {
        "glance": f"{status_icon} {total} items ({healthy} ok, {warning} warn, {error} error)",
        "overview": {
            "total": total,
            "healthy": healthy,
            "warning": warning,
            "error": error,
        },
    }


# ─────────────────────────────────────────────
# 4. Trie 索引（高效前缀查询）
# ─────────────────────────────────────────────

class _TrieNode:
    __slots__ = ("children", "is_terminal", "path")

    def __init__(self):
        self.children: Dict[str, _TrieNode] = {}
        self.is_terminal: bool = False
        self.path: Optional[str] = None


class _PathTrie:
    """路径前缀树，用于加速前缀查询和 glob 通配。"""

    def __init__(self):
        self._root = _TrieNode()

    def insert(self, path: str) -> None:
        node = self._root
        for seg in path_segments(path):
            if seg not in node.children:
                node.children[seg] = _TrieNode()
            node = node.children[seg]
        node.is_terminal = True
        node.path = normalize_path(path)

    def remove(self, path: str) -> bool:
        segments = path_segments(path)
        stack: List[Tuple[_TrieNode, str]] = []
        node = self._root
        for seg in segments:
            if seg not in node.children:
                return False
            stack.append((node, seg))
            node = node.children[seg]
        if not node.is_terminal:
            return False
        node.is_terminal = False
        node.path = None
        for parent, seg in reversed(stack):
            child = parent.children[seg]
            if not child.is_terminal and not child.children:
                del parent.children[seg]
            else:
                break
        return True

    def list_children(self, prefix: str) -> List[str]:
        """列出直接子路径（深度 +1）。"""
        node = self._navigate(prefix)
        if node is None:
            return []
        results = []
        for seg, child in node.children.items():
            if child.is_terminal:
                results.append(f"{normalize_path(prefix)}/{seg}")
        return results

    def list_descendants(self, prefix: str) -> List[str]:
        """列出所有后代路径（任意深度）。"""
        node = self._navigate(prefix)
        if node is None:
            return []
        results = []
        self._collect(node, results)
        return results

    def glob(self, pattern: str) -> List[str]:
        """通配符匹配：* 匹配单层，** 匹配任意深度。"""
        results: List[str] = []
        pat_segments = path_segments(pattern)
        self._glob_recursive(self._root, pat_segments, 0, "", results)
        return results

    def all_paths(self) -> List[str]:
        results: List[str] = []
        self._collect(self._root, results)
        return results

    def _navigate(self, prefix: str) -> Optional[_TrieNode]:
        node = self._root
        if not prefix or prefix == "":
            return node
        for seg in path_segments(prefix):
            if seg not in node.children:
                return None
            node = node.children[seg]
        return node

    def _collect(self, node: _TrieNode, results: List[str]) -> None:
        if node.is_terminal and node.path:
            results.append(node.path)
        for child in node.children.values():
            self._collect(child, results)

    def _glob_recursive(
        self, node: _TrieNode, patterns: List[str],
        idx: int, current_path: str, results: List[str],
    ) -> None:
        if idx >= len(patterns):
            if node.is_terminal and node.path:
                results.append(node.path)
            return

        seg = patterns[idx]

        if seg == "**":
            self._glob_recursive(node, patterns, idx + 1, current_path, results)
            for child_seg, child_node in node.children.items():
                child_path = f"{current_path}/{child_seg}" if current_path else child_seg
                self._glob_recursive(child_node, patterns, idx, child_path, results)
                self._glob_recursive(child_node, patterns, idx + 1, child_path, results)
        elif "*" in seg or "?" in seg:
            for child_seg, child_node in node.children.items():
                if fnmatch.fnmatch(child_seg, seg):
                    child_path = f"{current_path}/{child_seg}" if current_path else child_seg
                    self._glob_recursive(child_node, patterns, idx + 1, child_path, results)
        else:
            if seg in node.children:
                child_path = f"{current_path}/{seg}" if current_path else seg
                self._glob_recursive(node.children[seg], patterns, idx + 1, child_path, results)


# ─────────────────────────────────────────────
# 5. 查询结果集
# ─────────────────────────────────────────────

class QueryResult:
    """路径查询结果，支持链式过滤和批量披露。"""

    def __init__(self, items: List[Tuple[str, ContextEntry]]):
        self._items = items

    def filter(self, predicate: Callable[[str, ContextEntry], bool]) -> "QueryResult":
        return QueryResult([(p, e) for p, e in self._items if predicate(p, e)])

    def filter_tags(self, tags: List[str]) -> "QueryResult":
        tag_set = set(tags)
        return QueryResult([(p, e) for p, e in self._items if tag_set.issubset(set(e.tags))])

    def sort_by(self, key_fn: Callable[[Tuple[str, ContextEntry]], Any],
                reverse: bool = False) -> "QueryResult":
        return QueryResult(sorted(self._items, key=key_fn, reverse=reverse))

    def sort_by_path(self, reverse: bool = False) -> "QueryResult":
        return self.sort_by(lambda x: x[0], reverse=reverse)

    def limit(self, n: int) -> "QueryResult":
        return QueryResult(self._items[:n])

    def disclose_all(self, level: DetailLevel = DetailLevel.OVERVIEW) -> List[Dict[str, Any]]:
        return [{"path": p, **e.disclose(level)} for p, e in self._items]

    def paths(self) -> List[str]:
        return [p for p, _ in self._items]

    def entries(self) -> List[ContextEntry]:
        return [e for _, e in self._items]

    @property
    def items(self) -> List[Tuple[str, ContextEntry]]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Tuple[str, ContextEntry]]:
        return iter(self._items)

    def __repr__(self) -> str:
        return f"QueryResult(count={len(self._items)})"


# ─────────────────────────────────────────────
# 6. 核心存储（ContextStore）
# ─────────────────────────────────────────────

class ContextStore:
    """基于路径寻址的上下文存储。"""

    def __init__(self, title: str = "Context", description: str = ""):
        self.title = title
        self.description = description
        self._entries: Dict[str, ContextEntry] = {}
        self._schemas: Dict[str, SchemaNode] = {}
        self._trie = _PathTrie()

    def set(
        self,
        path: str,
        glance: str,
        overview: Union[Dict[str, Any], str, None] = None,
        detail: Any = None,
        tags: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "ContextStore":
        """写入或更新一个路径节点。"""
        norm = normalize_path(path)
        entry = ContextEntry(
            glance=glance,
            overview=overview,
            detail=detail,
            tags=tags or [],
            meta=meta or {},
        )
        self._entries[norm] = entry
        self._trie.insert(norm)
        return self

    def schema(
        self,
        path: str,
        glance: Optional[str] = None,
        overview: Union[Dict[str, Any], str, None] = None,
        tags: Optional[List[str]] = None,
        aggregator: Optional[Callable] = None,
    ) -> "ContextStore":
        """注册骨架节点。"""
        norm = normalize_path(path)
        self._schemas[norm] = SchemaNode(
            glance=glance,
            overview=overview,
            tags=tags or [],
            aggregator=aggregator,
        )
        self._trie.insert(norm)
        return self

    def _resolve_schema(self, path: str) -> Optional[ContextEntry]:
        """解析骨架节点为 ContextEntry。"""
        norm = normalize_path(path)
        sn = self._schemas.get(norm)
        if sn is None:
            return None
        child_paths = self._trie.list_children(norm)
        children = []
        for p in child_paths:
            entry = self._entries.get(p)
            if entry is None:
                entry = self._resolve_schema(p)
            if entry is not None:
                children.append((p, entry))
        return sn.resolve(children)

    def delete(self, path: str, recursive: bool = False) -> int:
        """删除节点。"""
        norm = normalize_path(path)
        count = 0
        if recursive:
            to_remove = [norm] + self._trie.list_descendants(norm)
            for p in to_remove:
                if p in self._entries:
                    del self._entries[p]
                    self._trie.remove(p)
                    count += 1
        else:
            if norm in self._entries:
                del self._entries[norm]
                self._trie.remove(norm)
                count = 1
        return count

    def get(self, path: str, level: DetailLevel = DetailLevel.OVERVIEW) -> Optional[Dict[str, Any]]:
        """获取单个路径的数据。"""
        norm = normalize_path(path)
        entry = self._entries.get(norm)
        if entry is None:
            entry = self._resolve_schema(norm)
        if entry is None:
            return None
        return {"path": norm, **entry.disclose(level)}

    def get_entry(self, path: str) -> Optional[ContextEntry]:
        """获取原始 ContextEntry 对象。"""
        norm = normalize_path(path)
        entry = self._entries.get(norm)
        if entry is None:
            entry = self._resolve_schema(norm)
        return entry

    def exists(self, path: str) -> bool:
        norm = normalize_path(path)
        return norm in self._entries or norm in self._schemas

    def children(self, prefix: str) -> QueryResult:
        """列出直接子路径。"""
        paths = self._trie.list_children(normalize_path(prefix))
        return self._to_query_result(paths)

    def descendants(self, prefix: str) -> QueryResult:
        """列出所有后代路径。"""
        paths = self._trie.list_descendants(normalize_path(prefix))
        return self._to_query_result(paths)

    def glob(self, pattern: str) -> QueryResult:
        """通配符查询。"""
        paths = self._trie.glob(normalize_path(pattern))
        seen = set()
        unique = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return self._to_query_result(unique)

    def glance(self, prefix: Optional[str] = None) -> List[str]:
        """快速扫描。"""
        if prefix:
            norm = normalize_path(prefix)
            descendant_paths = self._trie.list_descendants(norm)
            paths = []
            if self.exists(norm):
                paths.append(norm)
            paths.extend(p for p in descendant_paths if p != norm)
        else:
            paths = sorted(
                set(self._trie.all_paths()) | set(self._schemas.keys())
            )

        lines = []
        for p in paths:
            entry = self._entries.get(p) or self._resolve_schema(p)
            if entry:
                lines.append(f"{p} → {entry.glance}")
        return lines

    def tree(
        self,
        root: Optional[str] = None,
        level: DetailLevel = DetailLevel.OVERVIEW,
    ) -> Dict[str, Any]:
        """以嵌套字典形式返回树状结构。"""
        if root:
            norm = normalize_path(root)
            descendant_paths = self._trie.list_descendants(norm)
            all_paths_set = set(descendant_paths)
            if norm in self._entries or norm in self._schemas:
                all_paths_set.add(norm)
            all_paths = sorted(all_paths_set)
        else:
            all_paths = sorted(
                set(self._trie.all_paths()) | set(self._schemas.keys())
            )

        if not all_paths:
            return {}

        nodes_by_path: Dict[str, Dict[str, Any]] = {}
        roots: List[Dict[str, Any]] = []

        for p in all_paths:
            entry = self._entries.get(p) or self._resolve_schema(p)
            if entry is None:
                continue
            node_data = {"path": p, **entry.disclose(level), "children": []}
            nodes_by_path[p] = node_data

            par = parent_path(p)
            if par and par in nodes_by_path:
                nodes_by_path[par]["children"].append(node_data)
            else:
                roots.append(node_data)

        if len(roots) == 1:
            return roots[0]
        return {"path": root or "/", "children": roots}

    @property
    def count(self) -> int:
        all_keys = set(self._entries.keys()) | set(self._schemas.keys())
        return len(all_keys)

    def __len__(self) -> int:
        return self.count

    def __contains__(self, path: str) -> bool:
        return self.exists(path)

    def _to_query_result(self, paths: List[str]) -> QueryResult:
        items = [(p, self._entries[p]) for p in paths if p in self._entries]
        return QueryResult(items)


__all__ = [
    "ContextStore",
    "ContextEntry",
    "SchemaNode",
    "DetailLevel",
    "QueryResult",
    "count_aggregator",
    "normalize_path",
    "parent_path",
    "path_depth",
    "path_segments",
]
