# Agent Manager 重构文档

## 概述

本次重构移除了 `AgentManager` 类，改用更细粒度和模块化的架构，包括：
- `AgentRegistry` - 管理 agent 模板注册
- `AppAgentFactory` - 创建 agent 实例
- `AgentRuntime` - 管理运行中的 agent 生命周期
- `chat_helper` - 处理对话和消息

## 重构动机

### 旧架构的问题

1. **职责过重**: `AgentManager` 承担了太多职责
   - Agent 实例创建
   - 模板管理
   - 数据库操作
   - 执行控制

2. **耦合度高**: 所有功能都依赖 `AgentManager`
   - 难以独立测试
   - 难以扩展

3. **生命周期管理不清晰**: Agent 实例没有统一的生命周期管理

### 新架构的优势

1. **单一职责原则**: 每个组件只负责一件事
   - `AgentRegistry`: 管理模板注册
   - `AppAgentFactory`: 创建实例
   - `AgentRuntime`: 管理生命周期

2. **低耦合高内聚**: 组件之间独立，易于测试和扩展

3. **清晰的生命周期**: `AgentRuntime` 统一管理所有运行中的实例

## 架构对比

### 旧架构

```
AgentManager (管理一切)
├── 注册 agent 类型
├── 创建 agent 实例
├── 执行 agent
├── 流式输出
└── 数据库操作
```

### 新架构

```
AgentRegistry (模板注册)
├── 内存注册表
└── 数据库持久化

AppAgentFactory (实例创建)
├── 从 Registry 获取模板
└── 创建配置好的实例

AgentRuntime (生命周期管理)
├── attach(task_id, agent)
├── get(task_id)
└── release(task_id)

chat_helper (辅助函数)
├── prepare_conversation
├── create_message
└── stream_and_finalize
```

## 重构内容

### 1. dependencies/agents.py

**变更:**
- ❌ 移除 `get_agent_manager()`
- ✅ 添加 `get_agent_runtime()` - 返回全局 AgentRuntime 单例

**代码示例:**
```python
# 旧代码
agent_manager = Depends(get_agent_manager)
stream = agent_manager.stream_agent(agent_id, payload)

# 新代码
runtime = Depends(get_agent_runtime)
app_crud = Depends(get_app_crud)

app = await app_crud.get_app_by_id(app_id)
factory = AppAgentFactory(str(app.id), app.app_type, app.config)
agent = factory.create(payload)
runtime.attach(task_id, agent)
stream = agent.stream(payload)
```

### 2. workers/agent_worker.py

**变更:**
- ❌ 移除 `AgentManager` 依赖
- ✅ 使用 `AppCRUD` + `AgentRegistry` + `AppAgentFactory` + `AgentRuntime`

**工作流程:**
```python
# 1. 获取 App 配置
app = await app_crud.get_app_by_id(app_id)

# 2. 检查模板是否注册
if not AgentRegistry.is_registered(app.app_type):
    raise ValueError(...)

# 3. 创建 Factory 和 Agent 实例
factory = AppAgentFactory(
    appid=str(app.id),
    agent_type=app.app_type,
    app_config=app.config
)
agent_instance = factory.create(payload)

# 4. 附加到运行时
self.runtime.attach(task_id, agent_instance)

# 5. 流式执行
async for chunk in agent_instance.stream(payload):
    await self.publish_event(task_id, {"event": "chunk", "data": chunk})

# 6. 释放实例
self.runtime.release(task_id)
```

### 3. routers/agents/chat.py

**变更:**
- ❌ 移除 `AgentManager` 依赖
- ✅ 使用新架构组件
- 🔄 路由路径改为 `/{app_id}/direct` (统一使用 UUID)

**Direct Chat 新实现:**
```python
@router.post("/{app_id}/direct")
async def chat_with_agent_direct(
    app_id: UUID,
    payload: TextMessage,
    app_crud: AppCRUD = Depends(get_app_crud),
    runtime: AgentRuntime = Depends(get_agent_runtime),
):
    # 1. 获取 app
    app = await app_crud.get_app_by_id(app_id)

    # 2. 创建 agent
    factory = AppAgentFactory(str(app.id), app.app_type, app.config)
    agent = factory.create(payload_dict)

    # 3. 附加到运行时
    runtime.attach(message.id, agent)

    # 4. 流式执行
    stream = agent.stream(payload_dict)
    async for event in stream_and_finalize(...):
        yield event

    # 5. 清理
    runtime.release(message.id)
```

## 迁移指南

### 如果你在使用 AgentManager

**场景 1: 创建和执行 Agent**

```python
# ❌ 旧代码
async def run_agent(agent_id: str, input_data: dict):
    agent_manager = AgentManager(db_session)
    result = await agent_manager.run_agent(agent_id, input_data)
    return result

# ✅ 新代码
async def run_agent(app_id: UUID, input_data: dict):
    # 1. 获取 app 配置
    app_crud = AppCRUD(db_session)
    app = await app_crud.get_app_by_id(app_id)

    # 2. 创建 agent
    factory = AppAgentFactory(
        appid=str(app.id),
        agent_type=app.app_type,
        app_config=app.config
    )
    agent = factory.create(input_data)

    # 3. 执行
    result = await agent.run(input_data)
    return result
```

**场景 2: 流式输出**

```python
# ❌ 旧代码
async def stream_agent(agent_id: str, input_data: dict):
    agent_manager = AgentManager(db_session)
    async for chunk in agent_manager.stream_agent(agent_id, input_data):
        yield chunk

# ✅ 新代码
async def stream_agent(app_id: UUID, input_data: dict):
    # 1. 获取 app 和创建 agent
    app_crud = AppCRUD(db_session)
    app = await app_crud.get_app_by_id(app_id)

    factory = AppAgentFactory(str(app.id), app.app_type, app.config)
    agent = factory.create(input_data)

    # 2. 流式执行
    async for chunk in agent.stream(input_data):
        yield chunk
```

**场景 3: 在 Worker 中使用**

```python
# ❌ 旧代码
class MyWorker:
    async def process(self, task_id, app_id, payload):
        agent_manager = AgentManager(db_session)
        result = await agent_manager.run_agent(app_id, payload)

# ✅ 新代码
class MyWorker:
    def __init__(self):
        self.runtime = AgentRuntime()

    async def process(self, task_id, app_id, payload):
        # 获取配置
        app = await app_crud.get_app_by_id(app_id)

        # 创建实例
        factory = AppAgentFactory(str(app.id), app.app_type, app.config)
        agent = factory.create(payload)

        # 附加到运行时
        self.runtime.attach(task_id, agent)

        try:
            result = await agent.run(payload)
        finally:
            self.runtime.release(task_id)
```

## 新组件使用说明

### AgentRegistry

**用途:** 管理 agent 模板的注册

```python
from aiwen.services.agents.agent_registry import AgentRegistry

# 注册新的 agent 模板
await AgentRegistry.register(
    template_code="my_agent",
    template_name="My Agent",
    agent_cls=MyAgentClass,
    db_session=session,
    config={},
    enabled=True
)

# 检查是否已注册
if AgentRegistry.is_registered("my_agent"):
    agent_cls = AgentRegistry.get("my_agent")

# 列出所有模板
templates = AgentRegistry.list()
```

### AppAgentFactory

**用途:** 为单个应用创建配置好的 agent 实例

```python
from aiwen.services.agents.app_factory import AppAgentFactory

# 创建工厂
factory = AppAgentFactory(
    appid="uuid-string",
    agent_type="chat",  # 必须在 AgentRegistry 中注册
    app_config={"model": "gpt-4", "temperature": 0.7}
)

# 创建实例（会合并 app_config 和 payload）
agent = factory.create(payload={"query": "Hello"})

# 执行
result = await agent.run({"query": "Hello"})
```

### AgentRuntime

**用途:** 管理运行中的 agent 实例生命周期

```python
from aiwen.services.agents.runtime import AgentRuntime

runtime = AgentRuntime()

# 附加 agent 到运行时
runtime.attach(task_id, agent_instance)

# 获取运行中的 agent
agent = runtime.get(task_id)

# 释放 agent
runtime.release(task_id)

# 查看所有运行中的实例
all_instances = runtime._instances
```

### chat_helper

**用途:** 辅助处理对话和消息

```python
from aiwen.services.agents.chat.chat_helper import (
   create_conversation,
   create_message,
   stream_and_finalize
)

# 准备会话
conversation = await create_conversation(
   app_id=app_id,
   payload=payload,
   conversation_crud=conversation_crud
)

# 创建消息
message = await create_message(
   app_id=app_id,
   conversation_id=conversation.id,
   text_message=payload,
   message_crud=message_crud
)

# 流式执行并自动保存结果
async for event in stream_and_finalize(
        stream_iter=agent.stream(payload),
        conversation=conversation,
        message=message
):
   yield event
```

## 测试清单

- [ ] 队列式聊天: POST `/chat/{app_id}`
- [ ] 直接聊天: POST `/chat/{app_id}/direct`
- [ ] Agent Worker 任务处理
- [ ] Agent 实例生命周期管理
- [ ] 多个并发任务

## 常见问题

### Q: 为什么移除 AgentManager?
A: AgentManager 违反了单一职责原则，承担了太多职责。拆分后每个组件职责更清晰，更易于维护和测试。

### Q: 如何创建新的 agent 类型?
A:
1. 创建继承 `BaseAgentTemplate` 的类
2. 在应用启动时通过 `AgentRegistry.register()` 注册
3. 在数据库中创建对应的 `agent_template` 记录

### Q: AgentRuntime 是全局单例吗?
A: 在 `dependencies/agents.py` 中提供了全局单例 `_agent_runtime`，但你也可以创建独立的实例用于隔离场景。

### Q: 旧代码会立即失效吗?
A: `manager.py` 文件仍然存在，但不再被项目使用。你可以逐步迁移，但建议尽快完成。

## 后续计划

1. ✅ 完成核心重构
2. ⏳ 添加更多 agent 模板示例
3. ⏳ 完善单元测试
4. ⏳ 性能优化
5. ⏳ 文档完善

## 更新日志

### 2025-12-23 - v2.0.0
- 🎉 移除 `AgentManager`
- ✨ 引入 `AgentRegistry`、`AppAgentFactory`、`AgentRuntime`
- 🔧 重构 `dependencies/agents.py`
- 🔧 重构 `workers/agent_worker.py`
- 🔧 重构 `routers/agents/chat.py`
- 📝 添加完整的迁移文档
