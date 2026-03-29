# WorkspaceContext 缓存配置指南

## 配置参数

缓存使用以下环境变量配置：

```bash
# .env 文件
WORKSPACE_CONTEXT_CACHE_SIZE=200  # 最多缓存多少个 workspace（默认 200）
WORKSPACE_CONTEXT_CACHE_TTL=300   # 缓存过期时间（秒，默认 300 = 5分钟）
```

## 不同场景的推荐配置

### 开发环境

```bash
# .env.development
WORKSPACE_CONTEXT_CACHE_SIZE=100
WORKSPACE_CONTEXT_CACHE_TTL=600   # 10分钟，减少开发时的重复加载
```

**内存占用**: ~12.5MB
**命中率**: ~100% (单用户开发)
**适用**: 本地开发，快速迭代

---

### 生产环境（标准）

```bash
# .env.production
WORKSPACE_CONTEXT_CACHE_SIZE=200
WORKSPACE_CONTEXT_CACHE_TTL=300   # 5分钟
```

**内存占用**: ~25MB
**命中率**: ~60-80% (多用户)
**适用**: 大多数生产环境

---

### 高性能环境

```bash
# .env.high_performance
WORKSPACE_CONTEXT_CACHE_SIZE=500
WORKSPACE_CONTEXT_CACHE_TTL=600   # 10分钟
```

**内存占用**: ~62.5MB
**命中率**: ~90%+
**适用**: 高流量、高并发场景

---

### 低内存环境

```bash
# .env.low_memory
WORKSPACE_CONTEXT_CACHE_SIZE=50
WORKSPACE_CONTEXT_CACHE_TTL=180   # 3分钟
```

**内存占用**: ~6.25MB
**命中率**: ~30-50%
**适用**: 内存受限的服务器 (< 2GB)

---

## 内存占用计算

```
单个 workspace ≈ 125KB

内存占用 = CACHE_SIZE × 125KB

示例：
- 50 workspaces   = 6.25MB
- 100 workspaces  = 12.5MB
- 200 workspaces  = 25MB
- 500 workspaces  = 62.5MB
```

---

## 性能影响

### TTL 对性能的影响

| TTL | 优点 | 缺点 | 推荐场景 |
|-----|------|------|----------|
| 60s (1分钟) | 数据最新 | 缓存命中率低 | 实时性要求高 |
| 180s (3分钟) | 较新数据 | 中等命中率 | 快速变化的数据 |
| **300s (5分钟)** | **平衡** | **推荐** | **大多数场景** |
| 600s (10分钟) | 高命中率 | 可能看到旧数据 | 稳定的数据 |
| 1800s (30分钟) | 极高命中率 | 数据可能很旧 | 只读场景 |

### CACHE_SIZE 对性能的影响

| Size | 内存 | 命中率(假设100活跃workspace) | 推荐场景 |
|------|------|----------------------------|----------|
| 20 | 2.5MB | ~20% | 极低内存 |
| 50 | 6.25MB | ~50% | 低内存 |
| **200** | **25MB** | **~80%** | **标准** |
| 500 | 62.5MB | ~100% | 高性能 |

---

## 监控和调优

### 查看当前缓存状态

```python
from structure.utils.workspace_context_cache import get_cache_stats, get_cache_info

# 获取详细统计
stats = get_cache_stats()
print(stats)
# {
#     "size": 45,
#     "maxsize": 200,
#     "ttl": 300,
#     "usage_percent": 22.5,
#     "estimated_memory_mb": 5.62,
#     "workspaces": ["ws-001", "ws-002", ...]
# }

# 获取格式化信息
info = get_cache_info()
print(info)
# "WorkspaceContext Cache: 45/200 (22.5% full, ~5.6MB, TTL=300s)"
```

### 添加到 API 端点（可选）

```python
# src/structure/routers/monitoring.py
from fastapi import APIRouter
from structure.utils.workspace_context_cache import get_cache_stats

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/cache/stats")
async def get_workspace_cache_stats():
   """获取 WorkspaceContext 缓存统计"""
   return get_cache_stats()
```

访问：`http://localhost:8000/monitoring/cache/stats`

---

## 调优建议

### 场景 1：缓存命中率低 (< 50%)

**症状**：
```
stats = get_cache_stats()
# usage_percent 经常接近 100%
# 说明缓存容量不够
```

**解决方案**：
```bash
# 增加缓存大小
WORKSPACE_CONTEXT_CACHE_SIZE=500  # 原来 200
```

---

### 场景 2：内存使用过高

**症状**：
```bash
# 服务器内存使用率 > 80%
free -m
```

**解决方案**：
```bash
# 减小缓存大小
WORKSPACE_CONTEXT_CACHE_SIZE=50   # 原来 200

# 或减少 TTL（更快释放内存）
WORKSPACE_CONTEXT_CACHE_TTL=120   # 原来 300
```

---

### 场景 3：数据一致性问题

**症状**：
- 修改了 workspace context，但看到旧数据

**解决方案**：

**方案 A**：减少 TTL
```bash
WORKSPACE_CONTEXT_CACHE_TTL=60  # 1分钟过期
```

**方案 B**：手动失效缓存（推荐）

```python
from structure.utils.workspace_context_cache import invalidate_workspace_context_cache

# 在修改后立即失效
await update_workspace_context(...)
invalidate_workspace_context_cache(workspace_id)
```

**方案 C**：使用 force_reload
```python
# 强制从数据库重新加载
service = await get_cached_workspace_context(
    db, workspace_id, force_reload=True
)
```

---

## 最佳实践

### ✅ 推荐做法

1. **使用默认配置开始**
   ```bash
   WORKSPACE_CONTEXT_CACHE_SIZE=200
   WORKSPACE_CONTEXT_CACHE_TTL=300
   ```

2. **监控内存和命中率**
   - 定期检查 `get_cache_stats()`
   - 根据实际情况调整

3. **通过 WorkspaceContextService 修改数据**
   ```python
   # ✅ 自动同步到数据库
   await service.set(path, glance, overview, detail)
   ```

4. **在应用启动时打印配置**
   ```python
   from structure.utils.workspace_context_cache import get_cache_info
   logger.info(f"Cache initialized: {get_cache_info()}")
   ```

---

### ❌ 避免做法

1. **不要设置过大的 CACHE_SIZE**
   ```bash
   # ❌ 除非你确实有这么多活跃 workspace
   WORKSPACE_CONTEXT_CACHE_SIZE=10000  # 1.25GB！
   ```

2. **不要设置过长的 TTL**
   ```bash
   # ❌ 数据可能严重过期
   WORKSPACE_CONTEXT_CACHE_TTL=86400  # 24小时
   ```

3. **不要直接修改数据库绕过 service**
   ```python
   # ❌ 绕过缓存，导致不一致
   await db.execute(
       update(WorkspaceContext).where(...).values(glance="new")
   )
   # 缓存中仍是旧数据！

   # ✅ 使用 service
   await service.set(path, glance="new", ...)
   ```

---

## 故障排查

### 问题 1：缓存似乎不工作

**检查步骤**：

1. 确认环境变量已加载
   ```python
   import os
   print(os.getenv("WORKSPACE_CONTEXT_CACHE_SIZE"))
   ```

2. 检查缓存统计
   ```python
   from structure.utils.workspace_context_cache import get_cache_stats
   stats = get_cache_stats()
   print(f"Cache size: {stats['size']}/{stats['maxsize']}")
   ```

3. 添加日志
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   # 会看到 "WorkspaceContext cache hit" 或 "cache miss" 日志
   ```

---

### 问题 2：内存持续增长

**可能原因**：
- TTL 设置太长
- 有太多不同的 workspace 被访问

**排查**：
```python
stats = get_cache_stats()
print(f"Cached workspaces: {len(stats['workspaces'])}")
print(f"Memory: {stats['estimated_memory_mb']}MB")

# 如果 workspaces 数量持续增长，说明访问的 workspace 太多
```

**解决**：
```bash
# 减小 maxsize 或 TTL
WORKSPACE_CONTEXT_CACHE_SIZE=100
WORKSPACE_CONTEXT_CACHE_TTL=180
```

---

## 总结

### 快速配置指南

```bash
# 1. 在 .env 中添加（或使用默认值）
WORKSPACE_CONTEXT_CACHE_SIZE=200
WORKSPACE_CONTEXT_CACHE_TTL=300

# 2. 重启服务
make restart-all

# 3. 监控（可选）
curl http://localhost:8000/monitoring/cache/stats
```

### 性能提升

- **无缓存**: 每次 15ms
- **有缓存**: 首次 15ms，后续 0.1ms
- **20 次调用**: 300ms → 17ms (**94% 提升**)
- **内存占用**: 25MB (200 workspaces)

### 配置建议

- **开发**: maxsize=100, ttl=600
- **生产**: maxsize=200, ttl=300 ✅ **推荐**
- **高性能**: maxsize=500, ttl=600
- **低内存**: maxsize=50, ttl=180
