# Workspace Context Service - 持久化 + 缓存架构

## 🎯 设计目标

- ✅ **断电恢复**：服务重启后数据不丢失
- ✅ **查询快速**：内存操作，支持 glob/tree 等高级查询
- ✅ **自动同步**：修改自动保存到数据库

## 🏗️ 架构设计

```
┌─────────────────────────────────────────┐
│  WorkspaceContext 表 (PostgreSQL)       │  ← 持久化层（真实数据源）
│  - workspace_id                          │
│  - path, glance, summary, content       │
│  - tags, meta, s3_key                   │
│  - is_deleted, expires_at               │
└─────────────────────────────────────────┘
          ↓ 启动时加载
          ↓ 修改时同步
┌─────────────────────────────────────────┐
│  ContextStore (内存缓存)                │  ← 查询层（快速访问）
│  - glob("tools/**")                     │
│  - children("knowledge")                │
│  - tree(level=OVERVIEW)                 │
└─────────────────────────────────────────┘
```

## 💻 使用示例

### 1. 创建 Workspace 时

```python
from aiwen.services.workspace_context_service import WorkspaceContextService
from aiwen.extensions.database import get_session

async def create_workspace(workspace_id: str, user_id: str):
    async with get_session("aiwen") as session:
        # 1. 创建 service
        service = WorkspaceContextService(session, workspace_id)

        # 2. 加载初始数据（可能为空）
        await service.load()

        # 3. 从全局 Context 复制工具/知识等
        context_ids = [
            "tool_web_search_id",
            "tool_code_executor_id",
            "knowledge_python_guide_id",
        ]
        await service.copy_from_global_context(
            context_ids=context_ids,
            created_by=user_id
        )

        print("✓ Workspace 创建完成")
```

### 2. 服务重启后恢复

```python
async def restore_workspace(workspace_id: str):
    async with get_session("aiwen") as session:
        # 从数据库恢复到内存
        service = WorkspaceContextService(session, workspace_id)
        await service.load()  # ← 从 WorkspaceContext 表加载

        # 立即可用，数据完整
        tools = await service.glob("tools/**")
        print(f"✓ 恢复了 {len(tools)} 个工具")
```

### 3. 查询操作（快速，内存）

```python
async def query_workspace_contexts(workspace_id: str):
    async with get_session("aiwen") as session:
        service = WorkspaceContextService(session, workspace_id)
        await service.load()

        # Glob 查询 - 微秒级
        tools = await service.glob(f"{workspace_id}/tools/**")
        print(f"找到 {len(tools)} 个工具")

        # 获取树形结构
        tree = await service.tree(f"{workspace_id}/tools", level="overview")

        # 快速扫描
        glances = await service.glance(f"{workspace_id}/tools")
        for line in glances:
            print(line)

        # 查询子节点
        children = await service.children(f"{workspace_id}/knowledge")
```

### 4. 修改操作（自动同步到数据库）

```python
async def add_workspace_context(workspace_id: str):
    async with get_session("aiwen") as session:
        service = WorkspaceContextService(session, workspace_id)
        await service.load()

        # 添加新上下文（自动保存到数据库）
        await service.set(
            path=f"{workspace_id}/tools/new_tool",
            glance="New Tool — 新增的工具",
            overview={"功能": "测试", "版本": "1.0"},
            detail={"code": "..."},
            tags=["tool", "custom"],
            meta={"author": "user_123"},
            # 额外的 WorkspaceContext 字段
            content_type="application/json",
            created_by="user_123",
        )

        print("✓ 已保存到数据库")

        # 验证：重新加载后数据仍在
        service2 = WorkspaceContextService(session, workspace_id)
        await service2.load()
        result = await service2.get(f"{workspace_id}/tools/new_tool")
        assert result is not None
```

### 5. 删除操作（软删除）

```python
async def delete_workspace_context(workspace_id: str):
    async with get_session("aiwen") as session:
        service = WorkspaceContextService(session, workspace_id)
        await service.load()

        # 删除单个节点
        await service.delete(f"{workspace_id}/tools/old_tool")

        # 递归删除（删除节点及所有子节点）
        await service.delete(f"{workspace_id}/tools", recursive=True)

        # 数据库中 is_deleted=True（软删除，可恢复）
```

## 🔄 完整生命周期示例

```python
from aiwen.services.workspace_context_service import WorkspaceContextService

class WorkspaceManager:
    """Workspace 管理器（简化示例）"""

    def __init__(self, workspace_id: str):
        self.workspace_id = workspace_id
        self.context_service = None

    async def start(self, session):
        """启动 workspace（创建或恢复）"""
        self.context_service = WorkspaceContextService(session, self.workspace_id)
        await self.context_service.load()
        print(f"✓ Workspace {self.workspace_id} 已启动")

    async def execute_task(self, task_input: str):
        """执行任务（需要查询上下文）"""
        # 快速查询可用工具
        tools = await self.context_service.glob(f"{self.workspace_id}/tools/**")

        # 查询知识库
        knowledge = await self.context_service.children(f"{self.workspace_id}/knowledge")

        # 执行任务...
        result = f"使用了 {len(tools)} 个工具，参考了 {len(knowledge)} 个知识"

        # 保存执行历史
        await self.context_service.set(
            path=f"{self.workspace_id}/workspace_history/run_{task_id}",
            glance=f"Run #{task_id} — 已完成",
            overview={"状态": "完成", "耗时": "2.5s"},
            detail={"input": task_input, "output": result},
            tags=["history", "completed"],
        )

        return result

    async def shutdown(self):
        """关闭 workspace"""
        # 不需要特殊操作，所有修改已自动同步到数据库
        print(f"✓ Workspace {self.workspace_id} 已关闭（数据已保存）")

# 使用示例
async def main():
    async with get_session("aiwen") as session:
        # 创建 workspace
        ws = WorkspaceManager("ws_123")
        await ws.start(session)

        # 执行任务
        result = await ws.execute_task("分析 Python 代码")
        print(result)

        # 关闭
        await ws.shutdown()

        # ─── 服务重启 ───

        # 重新启动 workspace（数据完整恢复）
        ws2 = WorkspaceManager("ws_123")
        await ws2.start(session)  # ← 从数据库加载

        # 之前的历史记录仍在
        history = await ws2.context_service.glance(f"ws_123/workspace_history")
        print("历史记录:", history)
```

## ⚡ 性能对比

| 操作 | 纯数据库查询 | ContextStore 缓存 |
|------|-------------|-------------------|
| **路径前缀查询** | ~50ms | ~0.1ms (500x faster) |
| **Glob 通配** | 不支持/很慢 | ~0.2ms |
| **树形结构** | 多次查询 | ~1ms (单次) |
| **修改操作** | ~10ms | ~10ms (同步写入) |

## 🛡️ 故障恢复场景

### 场景 1：服务正常重启

```python
# 1. 服务关闭前
await service.set("path/to/data", glance="...")  # 已同步到 DB

# 2. 服务重启
service = WorkspaceContextService(session, workspace_id)
await service.load()  # ✓ 数据完整恢复

# 3. 继续使用
data = await service.get("path/to/data")  # ✓ 数据仍在
```

### 场景 2：服务崩溃（未提交事务）

```python
# 1. 修改操作
await service.set("path/to/data", glance="...")  # 已提交到 DB
await service.set("path/to/data2", glance="...")  # 已提交
# ... 崩溃 ...

# 2. 重启后
await service.load()  # ✓ 已提交的数据都恢复
```

### 场景 3：数据库故障

```python
# WorkspaceContext 表损坏？
# → 可以从 Context 表重新拷贝
await service.copy_from_global_context(context_ids)
```

## 📊 存储空间

```python
# WorkspaceContext 表大小估算
# - 每个 workspace: ~1000 个上下文条目
# - 每个条目: ~1KB (glance + summary + content)
# → 每个 workspace: ~1MB

# 100 个活跃 workspace = ~100MB
# 完全可接受，远小于纯内存方案的隐患
```

## ✅ 最佳实践

### 1. 启动时立即加载

```python
@app.on_event("startup")
async def startup():
    # 预加载活跃 workspace
    active_workspaces = await get_active_workspaces()
    for ws_id in active_workspaces:
        service = WorkspaceContextService(session, ws_id)
        await service.load()
        workspace_cache[ws_id] = service
```

### 2. 定期清理过期数据

```python
async def cleanup_expired_workspaces():
    # 删除过期的 workspace context
    stmt = select(WorkspaceContext).where(
        WorkspaceContext.expires_at < datetime.now(UTC)
    )
    # ... 执行删除
```

### 3. 监控缓存状态

```python
# 监控 ContextStore 大小
print(f"Workspace {ws_id} 缓存条目数: {service.store.count}")
```

## 🔧 故障排查

### 问题：数据未同步

```python
# 检查是否使用 service 方法（而非直接操作 store）
await service.set(...)  # ✓ 正确（自动同步）
service.store.set(...)  # ✗ 错误（不会同步）
```

### 问题：查询结果为空

```python
# 确保已加载
if not service._loaded:
    await service.load()
```

### 问题：内存占用过大

```python
# 释放 workspace cache
del workspace_cache[ws_id]

# 重新加载时再创建
service = WorkspaceContextService(session, ws_id)
await service.load()
```
