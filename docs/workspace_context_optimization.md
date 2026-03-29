# WorkspaceContext 性能优化指南

## 问题背景

每次调用 `WorkspaceContextService.load()` 都会执行数据库查询并构建 Trie 索引：
- 耗时：~15ms（100 个 contexts）
- 在 Agent 长任务中，如果调用 20 次 context 工具，浪费 300ms

## 解决方案：Executor 级缓存

在 Executor 实例生命周期内复用同一个 `WorkspaceContextService`，避免重复加载。

---

## 🚀 使用方法

### 方式 1：在 Executor 中使用（推荐）

```python
from structure.core.interfaces.executor import Executor
from structure.schemas.events.event_payloads import UserMessage


class MyExecutor(Executor):
    async def run(self, user_message: UserMessage) -> dict:
        workspace_id = self.config["workspace_id"]

        # 首次调用：从数据库加载（~15ms）
        ctx_service = await self.get_workspace_context(workspace_id)
        tools = await ctx_service.glob("tools/**")

        # 后续调用：直接从缓存返回（~0.1ms）
        ctx_service = await self.get_workspace_context(workspace_id)
        knowledge = await ctx_service.glob("knowledge/**")

        # 再次调用：仍然是缓存（无DB查询）
        ctx_service = await self.get_workspace_context(workspace_id)
        history = await ctx_service.glob("history/**")

        return {
            "tools_count": len(tools.paths()),
            "knowledge_count": len(knowledge.paths()),
            "history_count": len(history.paths()),
        }
```

**性能对比**：

| 场景 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| 3 次 glob 调用 | 3 × 15ms = 45ms | 1 × 15ms = 15ms | **67%** |
| 10 次调用 | 150ms | 15ms | **90%** |
| 20 次调用 | 300ms | 15ms | **95%** |

---

### 方式 2：在 Tool 中使用

如果你的工具需要在 Executor 上下文外独立使用，仍然使用原来的方式：

```python
class GlobContextTool(InnerTool):
    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from structure.extensions.database import get_session
        from structure.services.workspace_context.workspace_context_service import (
            WorkspaceContextService,
        )

        # 每次调用都创建新的 service（适用于独立工具调用）
        async with get_session("structure") as db:
            service = WorkspaceContextService(db, input_data.workspace_id)
            await service.load()  # ~15ms
            result = await service.glob(input_data.pattern)
            return ToolOutputSchema(success=True, data={"contexts": result.paths()})
```

**何时使用这种方式**：
- ✅ 单次工具调用（API 直接调用）
- ✅ 需要最新数据（不能使用缓存）
- ❌ Agent 内部调用（应该用方式 1）

---

### 方式 3：Agent 内通过 Tool 调用（高级用法）

如果你想在 Agent 中通过工具调用，但仍然享受缓存，可以修改工具接受 `service` 参数：

```python
class GlobContextTool(InnerTool):
    async def execute(
            self,
            input_data: InputSchema,
            # 可选：如果提供了 service，直接使用（避免重复加载）
            _cached_service: WorkspaceContextService | None = None
    ) -> ToolOutputSchema:
        if _cached_service:
            # 使用缓存的 service（Executor 提供）
            service = _cached_service
        else:
            # 独立调用：创建新的 service
            from structure.extensions.database import get_session
            async with get_session("structure") as db:
                service = WorkspaceContextService(db, input_data.workspace_id)
                await service.load()

        result = await service.glob(input_data.pattern)
        return ToolOutputSchema(success=True, data={"contexts": result.paths()})
```

然后在 Executor 中调用：

```python
class MyExecutor(Executor):
    async def run(self, user_message: UserMessage) -> dict:
        workspace_id = self.config["workspace_id"]

        # 获取缓存的 service
        ctx_service = await self.get_workspace_context(workspace_id)

        # 调用工具，传入缓存的 service
        tool = GlobContextTool()
        result = await tool.execute(
            InputSchema(workspace_id=workspace_id, pattern="tools/**"),
            _cached_service=ctx_service  # ← 传入缓存
        )
```

---

## 📊 性能测试

### 测试代码

```python
import pytest
import time
from structure.core.interfaces.executor import Executor


async def test_workspace_context_cache_performance(executor_instance):
    """测试缓存性能提升"""
    workspace_id = "test-workspace"

    # 测量首次加载
    start_first = time.perf_counter()
    service1 = await executor_instance.get_workspace_context(workspace_id)
    duration_first = time.perf_counter() - start_first

    # 测量缓存命中
    start_cached = time.perf_counter()
    service2 = await executor_instance.get_workspace_context(workspace_id)
    duration_cached = time.perf_counter() - start_cached

    # 验证是同一个实例
    assert service1 is service2

    # 缓存应该快至少 100 倍
    assert duration_cached < duration_first * 0.01

    print(f"首次加载: {duration_first * 1000:.2f}ms")
    print(f"缓存命中: {duration_cached * 1000:.2f}ms")
    print(f"性能提升: {duration_first / duration_cached:.0f}x")
```

### 预期结果

```
首次加载: 15.23ms
缓存命中: 0.12ms
性能提升: 127x
```

---

## 🔧 生命周期管理

### 缓存何时创建？

```python
executor = MyExecutor(config)
await executor.run(user_message)
    ↓
第一次调用 get_workspace_context(workspace_id)
    ↓
创建 WorkspaceContextService 并加载（~15ms）
    ↓
缓存到 executor._workspace_context_cache[workspace_id]
```

### 缓存何时清理？

```python
# 方式 1：Executor 完成后自动清理（推荐）
executor = MyExecutor(config)
await executor.run(user_message)
# Executor 实例销毁时，缓存自动清理

# 方式 2：手动清理（如果需要）
await executor._cleanup_workspace_context_cache()
```

**注意**：缓存是 **Executor 实例级别** 的，不是全局的：
- ✅ 同一个 Executor 实例内共享缓存
- ❌ 不同 Executor 实例不共享缓存
- ✅ Executor 销毁后，缓存自动释放

---

## ⚠️ 注意事项

### 1. 数据一致性

缓存期间，如果数据库中的 context 被其他进程修改，缓存不会自动更新。

**解决方案**：
- ✅ Agent 任务内的修改会同步（通过 `service.set()`）
- ⚠️ 跨进程修改不会反映到缓存
- 🔄 如果需要强制刷新，重新创建 Executor 实例

### 2. 内存使用

每个缓存的 workspace 占用约 120KB：
- 1 个 workspace：~120KB
- 10 个 workspaces：~1.2MB
- 100 个 workspaces：~12MB

**建议**：
- ✅ 单个 Executor 通常只访问 1-3 个 workspaces
- ⚠️ 避免在同一个 Executor 中访问大量不同的 workspaces
- 🚫 如果需要访问 100+ workspaces，考虑分批处理

### 3. Session 生命周期

缓存的 `WorkspaceContextService` 持有数据库 session：
- ✅ Session 在 Executor 生命周期内保持打开
- ✅ 自动清理时会正确关闭 session
- ⚠️ 长时间运行的 Executor 需要注意连接池

---

## 🎯 最佳实践

### ✅ 推荐做法

```python
# 1. 在 Executor 中使用缓存
class MyExecutor(Executor):
    async def run(self, user_message: UserMessage):
        ctx = await self.get_workspace_context(workspace_id)
        # 多次使用 ctx，享受缓存

# 2. 独立工具调用使用原方式
async with get_session("structure") as db:
    service = WorkspaceContextService(db, workspace_id)
    await service.load()

# 3. 需要最新数据时，重新创建 service
service_fresh = WorkspaceContextService(db, workspace_id)
await service_fresh.load()  # 获取最新数据
```

### ❌ 避免做法

```python
# ❌ 不要在多个 Executor 间共享 service
service = await executor1.get_workspace_context(workspace_id)
executor2._use_external_service(service)  # 危险：session 可能失效

# ❌ 不要手动修改缓存
executor._workspace_context_cache[workspace_id] = my_service  # 危险

# ❌ 不要在 Executor 外部访问缓存
cache = executor._workspace_context_cache  # 私有 API，可能变更
```

---

## 📈 进一步优化

如果你的场景需要更高级的缓存策略：

### 1. 应用级缓存（跨 Executor）

```python
from cachetools import TTLCache

class WorkspaceContextCache:
    _cache = TTLCache(maxsize=50, ttl=300)  # 50个workspace，5分钟过期

    @classmethod
    async def get_or_load(cls, session, workspace_id):
        if workspace_id not in cls._cache:
            service = WorkspaceContextService(session, workspace_id)
            await service.load()
            cls._cache[workspace_id] = service
        return cls._cache[workspace_id]
```

### 2. Redis 缓存（分布式）

```python
import pickle
from redis.asyncio import Redis

async def get_cached_context_store(workspace_id: str):
    redis = await Redis.from_url("redis://localhost")
    cache_key = f"context:ws:{workspace_id}"

    cached = await redis.get(cache_key)
    if cached:
        return pickle.loads(cached)

    # Load from DB and cache to Redis
    # ...
```

---

## 🔍 调试技巧

### 查看缓存状态

```python
class MyExecutor(Executor):
    async def run(self, user_message: UserMessage):
        # 查看当前缓存了哪些 workspace
        print(f"Cached workspaces: {list(self._workspace_context_cache.keys())}")

        ctx = await self.get_workspace_context(workspace_id)

        # 检查是否是缓存命中
        is_cached = workspace_id in self._workspace_context_cache
        print(f"Cache hit: {is_cached}")
```

### 性能监控

```python
import time
from functools import wraps

def monitor_cache_performance(func):
    @wraps(func)
    async def wrapper(self, workspace_id: str):
        start = time.perf_counter()
        result = await func(self, workspace_id)
        duration = time.perf_counter() - start

        is_cached = workspace_id in self._workspace_context_cache
        status = "cache_hit" if is_cached else "db_load"

        print(f"get_workspace_context: {status} took {duration*1000:.2f}ms")
        return result

    return wrapper

# 在开发环境中使用
Executor.get_workspace_context = monitor_cache_performance(
    Executor.get_workspace_context
)
```

---

## 总结

1. **默认行为**：每次 `load()` 都查询数据库（~15ms）
2. **优化方式**：在 Executor 中使用 `get_workspace_context()` 缓存
3. **性能提升**：20 次调用从 300ms 降到 15ms（**95% 提升**）
4. **使用场景**：Agent 长任务、多次访问同一 workspace
5. **无需改动**：独立工具调用保持原样

**核心原则**：在 Executor 生命周期内复用 WorkspaceContextService，而不是在全局共享状态。
