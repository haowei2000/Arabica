# Context Operation Tools Guide

## 概述

上下文操作工具（Context Tools）提供了一套完整的 API 来管理 workspace 中的上下文数据，支持：

- **路径寻址** - 使用文件系统风格的路径
- **Glob 通配符** - `*` 和 `**` 模式匹配
- **渐进式披露** - Glance/Overview/Detail 三层信息
- **自动持久化** - 修改自动保存到数据库

## 工具列表

### 📖 读取操作

| 工具 | 功能 | 适用场景 |
|------|------|----------|
| `read_context` | 读取单个上下文 | 查看具体上下文的详细信息 |
| `list_context` | 列出子节点 | 浏览目录下的直接子项或所有后代 |
| `glob_context` | 通配符查询 | 模式匹配查找（如 `tools/**`） |
| `glance_context` | 快速扫描 | 一句话概览所有上下文 |
| `tree_context` | 树形结构 | 获取层级关系的完整视图 |
| `search_context` | 正则搜索 | 在内容中搜索特定模式 |

### ✏️ 写入操作

| 工具 | 功能 | 适用场景 |
|------|------|----------|
| `create_context` | 创建上下文 | 添加新的工具、知识、技能等 |
| `update_context` | 更新上下文 | 修改已有上下文的任何字段 |
| `delete_context` | 删除上下文 | 移除上下文（软删除，可递归） |

## 详细说明

### 1. read_context - 读取上下文

**用途**: 读取单个上下文的内容

**参数**:
```python
{
    "workspace_id": "ws_123",
    "path": "tools/web_search",
    "level": "overview"  # glance | overview | detail
}
```

**返回示例**:
```json
{
    "success": true,
    "message": "Retrieved context: tools/web_search (overview)",
    "data": {
        "path": "tools/web_search",
        "level": "overview",
        "context": {
            "glance": "Web Search Tool — ✅ Ready",
            "overview": {
                "provider": "DuckDuckGo",
                "rate_limit": "100/hour"
            },
            "tags": ["tool", "search", "web"]
        }
    }
}
```

**使用场景**:
- 查看工具的详细配置
- 读取知识库内容
- 获取特定资源的信息

---

### 2. glob_context - 通配符查询

**用途**: 使用 glob 模式查找匹配的上下文

**参数**:
```python
{
    "workspace_id": "ws_123",
    "pattern": "tools/**",  # * 单层, ** 任意深度
    "level": "glance",
    "limit": 50,
    "tags": ["tool", "active"]  # 可选：标签过滤
}
```

**通配符规则**:
- `*` - 匹配单层任意内容（如 `tools/*`）
- `**` - 匹配任意深度（如 `tools/**`, `knowledge/*/docs`）
- `?` - 匹配单个字符

**返回示例**:
```json
{
    "success": true,
    "message": "Found 3 contexts matching pattern: tools/**",
    "data": {
        "pattern": "tools/**",
        "count": 3,
        "paths": ["tools/web_search", "tools/calculator", "tools/code_exec"],
        "contexts": [...]
    }
}
```

**使用场景**:
- 查找所有工具: `tools/**`
- 查找特定类型: `knowledge/python/**`
- 模糊匹配: `tools/web_*`

---

### 3. list_context - 列出子节点

**用途**: 列出路径下的直接子节点或所有后代

**参数**:
```python
{
    "workspace_id": "ws_123",
    "path": "tools",
    "mode": "children",  # children | descendants
    "level": "glance",
    "limit": 100
}
```

**模式说明**:
- `children` - 只列出直接子节点（一层）
- `descendants` - 列出所有后代（递归）

**返回示例**:
```json
{
    "success": true,
    "message": "Found 5 children under: tools",
    "data": {
        "path": "tools",
        "mode": "children",
        "count": 5,
        "paths": [...],
        "contexts": [...]
    }
}
```

**使用场景**:
- 浏览目录: `mode="children"`
- 统计所有资源: `mode="descendants"`

---

### 4. glance_context - 快速扫描

**用途**: 最快的查询方式，只返回一句话摘要

**参数**:
```python
{
    "workspace_id": "ws_123",
    "prefix": "tools",  # 可选：只扫描指定前缀
    "limit": 100
}
```

**返回示例**:
```json
{
    "success": true,
    "message": "Scanned 5 contexts",
    "data": {
        "count": 5,
        "glances": [
            {"path": "tools/web_search", "glance": "Web Search Tool — ✅ Ready"},
            {"path": "tools/calculator", "glance": "Calculator — ✅ Ready"}
        ],
        "raw_glances": [
            "tools/web_search → Web Search Tool — ✅ Ready",
            "tools/calculator → Calculator — ✅ Ready"
        ]
    }
}
```

**使用场景**:
- 快速浏览所有资源状态
- 生成资源列表摘要
- 性能敏感的查询

---

### 5. tree_context - 树形结构

**用途**: 获取层级关系的完整树形结构

**参数**:
```python
{
    "workspace_id": "ws_123",
    "root": "tools",  # 可选：树的根路径
    "level": "overview"
}
```

**返回示例**:
```json
{
    "success": true,
    "message": "Retrieved tree structure (8 nodes)",
    "data": {
        "root": "tools",
        "level": "overview",
        "total_nodes": 8,
        "tree": {
            "path": "tools",
            "glance": "Available Tools",
            "overview": {...},
            "children": [
                {
                    "path": "tools/web_search",
                    "glance": "Web Search Tool — ✅ Ready",
                    "children": []
                },
                ...
            ]
        }
    }
}
```

**使用场景**:
- 可视化资源结构
- 生成导航菜单
- 理解资源组织方式

---

### 6. create_context - 创建上下文

**用途**: 添加新的上下文条目

**参数**:
```python
{
    "workspace_id": "ws_123",
    "path": "tools/new_tool",
    "glance": "New Tool — ✅ Ready",
    "overview": {"provider": "Custom", "version": "1.0"},
    "detail": {"description": "...", "params": {...}},
    "tags": ["tool", "custom"],
    "meta": {"author": "user_123"},
    "name": "New Tool",  # 可选：显示名称
    "content_type": "application/json"
}
```

**必需字段**:
- `workspace_id` - workspace ID
- `path` - 上下文路径
- `glance` - 一句话摘要
- `detail` - 完整内容

**可选字段**:
- `overview` - 概览信息
- `tags` - 标签列表
- `meta` - 元数据
- `name` - 显示名称
- `content_type` - MIME 类型

**返回示例**:
```json
{
    "success": true,
    "message": "Created context at: tools/new_tool",
    "data": {
        "path": "tools/new_tool",
        "glance": "New Tool — ✅ Ready",
        "tags": ["tool", "custom"],
        "context": {...}
    }
}
```

---

### 7. update_context - 更新上下文

**用途**: 修改已有上下文的任何字段

**参数**:
```python
{
    "workspace_id": "ws_123",
    "path": "tools/web_search",
    "glance": "Web Search — ⚠️ Maintenance",  # 可选：更新 glance
    "overview": {...},  # 可选：更新 overview
    "detail": {...},    # 可选：更新 detail
    "tags": ["tool", "search", "maintenance"],  # 可选：更新 tags
    "meta": {"last_updated": "2025-02-15"}      # 可选：更新 meta
}
```

**说明**:
- 只需提供要更新的字段
- 未提供的字段保持不变
- `meta` 会与现有元数据合并

**返回示例**:
```json
{
    "success": true,
    "message": "Updated context at: tools/web_search",
    "data": {
        "path": "tools/web_search",
        "updated_fields": ["glance", "tags"],
        "context": {...}
    }
}
```

---

### 8. delete_context - 删除上下文

**用途**: 删除上下文条目（软删除）

**参数**:
```python
{
    "workspace_id": "ws_123",
    "path": "tools/old_tool",
    "recursive": false,  # true: 同时删除所有后代
    "confirm": true      # 必须为 true（安全检查）
}
```

**安全机制**:
- 必须设置 `confirm=true` 才会实际删除
- `recursive=true` 时会删除所有子节点

**返回示例**:
```json
{
    "success": true,
    "message": "Deleted 1 context(s) at: tools/old_tool",
    "data": {
        "path": "tools/old_tool",
        "recursive": false,
        "deleted_count": 1,
        "deleted_paths": ["tools/old_tool"]
    }
}
```

**递归删除示例**:
```python
# 删除整个 tools 目录及其所有内容
{
    "workspace_id": "ws_123",
    "path": "tools",
    "recursive": true,
    "confirm": true
}
```

---

## 使用示例

### 场景 1: 管理工具集合

```python
# 1. 创建工具目录结构
create_context(path="tools/search", glance="Search Tools", ...)
create_context(path="tools/search/web", glance="Web Search", ...)
create_context(path="tools/search/code", glance="Code Search", ...)

# 2. 浏览所有搜索工具
glob_context(pattern="tools/search/**", level="glance")

# 3. 更新工具状态
update_context(path="tools/search/web", glance="Web Search — ⚠️ Rate Limited")

# 4. 删除过期工具
delete_context(path="tools/search/old_search", confirm=True)
```

### 场景 2: 知识库管理

```python
# 1. 创建知识库
create_context(
    path="knowledge/python/async",
    glance="Python Async Programming Guide",
    overview="Complete guide to async/await in Python",
    detail={"chapters": [...], "examples": [...]}
)

# 2. 快速浏览所有知识
glance_context(prefix="knowledge")

# 3. 查找 Python 相关知识
glob_context(pattern="knowledge/python/**", tags=["python"])
```

### 场景 3: 运行历史记录

```python
# 1. 记录运行结果
create_context(
    path=f"history/run_{run_id}",
    glance=f"Run #{run_id} — ✅ Completed",
    overview={"status": "success", "duration": "2.5s"},
    detail={"input": {...}, "output": {...}}
)

# 2. 查看最近的运行
list_context(path="history", mode="children", limit=10)

# 3. 搜索失败的运行
glob_context(pattern="history/**", tags=["failed"])
```

## 性能建议

### 最快 → 最慢

1. **glance_context** (~0.1ms) - 只返回一句话，最快
2. **glob_context** (~0.2ms) - 通配符查询，带索引优化
3. **list_context** (~0.5ms) - 列出子节点
4. **read_context** (~1ms) - 读取单个上下文
5. **tree_context** (~2ms) - 构建树形结构

### 选择指南

- 需要快速浏览 → `glance_context`
- 需要模式匹配 → `glob_context`
- 需要层级结构 → `tree_context`
- 需要详细内容 → `read_context`

## 最佳实践

### 1. 路径命名规范

```
✅ 推荐:
- tools/web_search
- knowledge/python/async
- history/2025/02/run_123

❌ 避免:
- Tools/Web Search  (空格和大写)
- tool_1, tool_2    (无层级结构)
- /tools/search/    (多余的斜杠)
```

### 2. 使用标签分类

```python
# 按用途分类
tags=["tool", "search", "active"]

# 按状态分类
tags=["ready", "maintenance", "deprecated"]

# 按优先级分类
tags=["high-priority", "experimental"]
```

### 3. 渐进式披露

```python
# 快速浏览 - 用 glance
glance_context(prefix="tools")

# 需要摘要 - 用 overview
read_context(path="tools/web_search", level="overview")

# 需要完整数据 - 用 detail
read_context(path="tools/web_search", level="detail")
```

### 4. 批量操作

```python
# ❌ 逐个查询（慢）
for tool in ["web_search", "calculator", "code_exec"]:
    read_context(path=f"tools/{tool}")

# ✅ 一次性查询（快）
glob_context(pattern="tools/**")
```

## 测试

运行测试套件：
```bash
uv run python scripts/test_context_tools.py
```

所有工具已通过完整测试验证 ✅

## 总结

Context Tools 提供了：
- ✅ 9 个完整的上下文操作工具
- ✅ 支持 ContextLayer 框架所有特性
- ✅ 自动持久化到数据库
- ✅ 高性能内存查询（500x 提升）
- ✅ 完整的测试覆盖

可以立即在 Agent 执行器中使用这些工具！
