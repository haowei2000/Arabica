# 问题修复总结

## 修复时间
2025-12-23

## 修复的问题

### 1. ❌ 'App' object has no attribute 'app_type'

**问题原因：**
`App` 模型没有 `app_type` 字段，正确的关系是通过 `agent_template_id` 关联到 `AgentTemplate`。

**解决方案：**
```python
# ❌ 错误的方式
if not AgentRegistry.is_registered(app.app_type):
    ...

# ✅ 正确的方式
template = await template_crud.get_template_by_id(app.agent_template_id)
if not AgentRegistry.is_registered(template.template_code):
    ...
```

**修改的文件：**
- `src/structure/workers/agent_worker.py`
- `src/structure/routers/agents/chat.py`

---

### 2. ❌ Agent type 'DEFAULT001' is not registered in AgentRegistry

**问题原因：**
Worker 是独立进程，不会执行 FastAPI 的 `lifespan` 钩子，因此 `AgentRegistry` 在 Worker 中是空的。

**解决方案：**
在 Worker 启动脚本中添加 AgentRegistry 初始化：

```python
# src/scripts/run_agent_worker.py
from structure.services.executor.executor_registry import init_executor_registry


async def main():
   # 初始化 Redis
   await init_redis_client()

   # 初始化 Agent Registry（新增）
   await init_executor_registry()

   # 启动 worker
   await start_worker(redis_client, db_factory)
```

**修改的文件：**
- `src/scripts/run_agent_worker.py`
- `src/scripts/test_agent_registry.py` (新建)

---

### 3. ✨ 参数重命名：agent_type → template_code

**改进原因：**
`agent_type` 这个名字容易误导，实际上应该是 `template_code`（Agent 模板代码）。

**修改内容：**
```python
# ❌ 旧的参数名
class AppAgentFactory:
    def __init__(self, appid: str, agent_type: str, app_config: dict):
        ...

# ✅ 新的参数名
class AppAgentFactory:
    def __init__(self, appid: str, template_code: str, app_config: dict):
        ...
```

**修改的文件：**
- `src/structure/services/agents/app_factory.py`
- `src/structure/workers/agent_worker.py`
- `src/structure/routers/agents/chat.py`

---

## 数据模型关系图

```
┌─────────────────┐
│      App        │
├─────────────────┤
│ id: UUID        │
│ app_code: str   │
│ agent_template_id: UUID ────┐
│ config: dict    │            │
└─────────────────┘            │
                               │ 关联
                               ▼
                    ┌─────────────────────┐
                    │  AgentTemplate      │
                    ├─────────────────────┤
                    │ id: UUID            │
                    │ template_code: str ◄──── 用于 AgentRegistry
                    │ template_name: str  │
                    │ config: dict        │
                    └─────────────────────┘
                               │
                               │ 在 Registry 中注册
                               ▼
                    ┌─────────────────────┐
                    │  AgentRegistry      │
                    ├─────────────────────┤
                    │ DEFAULT001: Class   │
                    │ NL2SQL001: Class    │
                    │ ...                 │
                    └─────────────────────┘
```

## 完整工作流程

### FastAPI 应用启动
```python
# core/lifespan.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化
    await init_redis_client()
    await initialize_databases()
    await create_admin_user()
    await init_agent_registry()  # ← 注册所有模板

    yield

    # 关闭时清理
    ...
```

### Worker 启动
```python
# scripts/run_agent_worker.py
async def main():
    # 初始化 Redis
    await init_redis_client()

    # 初始化 Agent Registry（关键！）
    await init_agent_registry()  # ← Worker 需要独立初始化

    # 启动 worker
    await start_worker(redis_client, db_factory)
```

### Worker 处理任务

```python
# workers/task_worker.py
async def process_task(self, message_data):
    # 1. 获取 App
    app = await app_crud.get_app(app_id)

    # 2. 获取 AgentTemplate
    template = await template_crud.get_template_by_id(app.agent_template_id)

    # 3. 检查是否在 Registry 中注册
    if not AgentRegistry.is_registered(template.template_code):
        raise ValueError(...)

    # 4. 创建 Factory 和 Agent 实例
    factory = AppAgentFactory(
        appid=str(app.id),
        template_code=template.template_code,  # ← 使用 template_code
        app_config=app.config or {}
    )
    agent = factory.create(payload)

    # 5. 执行
    self.runtime.attach(task_id, agent)
    async for chunk in agent.stream(payload):
        await self.publish_event(task_id, {"event": "chunk", "data": chunk})
    self.runtime.release(task_id)
```

## 测试步骤

### 1. 测试 AgentRegistry
```bash
python src/scripts/test_agent_registry.py
```

预期输出：
```
✓ DEFAULT001 is registered
✓ Agent class: <class 'structure.services.agents.agent_template.default.concrete.DefaultAgentTemplate'>
✓ All tests passed!
```

### 2. 启动 Worker
```bash
# 方法 1: 直接启动（推荐）
python src/structure/worker_cli.py

# 方法 2: 使用模块方式启动
python -m structure.start_worker

# 方法 3: 后台运行
nohup python src/structure/worker_cli.py > logs/agent_worker.log 2>&1 &
```

预期日志：
```
Initializing Agent Registry...
=== Registering default agent templates ===
✓ Registered new agent template: DEFAULT001 (id: ...)
=== Agent template registration complete: 1 succeeded, 0 failed ===
Agent Registry initialized successfully
Starting worker loop...
```

### 3. 测试聊天
发送 POST 请求到 `/chat/{app_id}`：
```json
{
  "query": "你好"
}
```

预期：正常流式返回响应

## 修改文件清单

### 核心修复
- ✅ `src/structure/workers/agent_worker.py`
- ✅ `src/structure/routers/agents/chat.py`
- ✅ `src/structure/services/agents/app_factory.py`

### 启动脚本
- ✅ `src/scripts/run_agent_worker.py`
- ✅ `src/scripts/test_agent_registry.py` (新建)

### 文档
- ✅ `docs/AGENT_WORKER.md`
- ✅ `docs/REFACTORING_MANAGER.md`
- ✅ `docs/FIXES_SUMMARY.md` (本文件)

## 后续建议

1. **重启所有服务**
   ```bash
   # 重启 FastAPI 应用
   pkill -f uvicorn
   # 重新启动应用

   # 重启 Worker
   pkill -f agent_worker
   python src/scripts/run_agent_worker.py
   ```

2. **监控日志**
   - 检查 Worker 启动日志，确认 AgentRegistry 已初始化
   - 监控任务处理日志，确认没有错误

3. **添加更多模板**
   - 在 `agent_template/` 目录下创建新模板
   - 在 `agent_registry.py` 的 `register_default_templates()` 中注册
   - 记得在 FastAPI 应用和 Worker 中都会自动注册

## 架构优势

### 清晰的职责分离
- **AgentRegistry**: 管理模板注册（内存 + 数据库）
- **AppAgentFactory**: 创建 agent 实例
- **AgentRuntime**: 管理运行中的实例生命周期
- **CRUD**: 数据库操作

### 灵活性
- 可以动态添加新的 agent 模板
- 每个 app 可以有自己的配置
- 支持模板继承和复用

### 可测试性
- 每个组件独立，易于单元测试
- 提供了测试脚本验证注册状态

## 常见问题

**Q: Worker 启动时如何确认 AgentRegistry 已初始化？**
A: 查看启动日志，应该有 "Agent Registry initialized successfully" 消息。

**Q: 如何添加新的 agent 模板？**
A:
1. 创建继承 `BaseAgentTemplate` 的类
2. 在 `register_default_templates()` 中添加注册代码
3. 重启 FastAPI 应用和 Worker

**Q: 为什么要将 `agent_type` 改为 `template_code`？**
A: `template_code` 更准确地反映了这个参数的含义（模板代码），避免与其他概念混淆。
