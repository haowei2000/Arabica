# Executor 开发指南

## 概述

Executor 是 Aiwen 中 Agent 的执行引擎。每种 Executor 代表一种 Agent 行为模式（如对话型、工作流型、NL2SQL 型等）。系统通过 `ExecutorRegistry` 统一管理 Executor 的注册、发现、持久化与实例化。

**核心架构**

```
┌─────────────────────────────────────────────┐
│            RegistryManager (单例)            │
│  协调所有 Registry，统一同步到数据库            │
└──────┬──────────────────────┬───────────────┘
       │                      │
┌──────▼──────┐       ┌──────▼──────────┐
│ToolRegistry │       │ExecutorRegistry │
└─────────────┘       └──────┬──────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
       DefaultExecutor  ConflictExecutor  YourExecutor
```

---

## 1. Executor 生命周期

```
代码定义 → 自动发现 → 内存注册 → 数据库同步 → 运行时实例化 → 事件流式执行
```

### 1.1 代码定义

每个 Executor 是一个继承自 `Executor` ABC 的 Python 类，通过 `@register_executor` 装饰器声明注册。

```python
from aiwen.registries import register_executor
from aiwen.core.interfaces import Executor, AgentEvent


@register_executor
class MyExecutor(Executor):
    TEMPLATE: ClassVar[dict[str, Any]] = {
        "template_code": "MyExecutor",  # 唯一标识（主键）
        "template_name": "My Executor",  # 显示名称
        "enabled": True,
        "version": 1,
        "config": {},  # 默认配置
    }
```

### 1.2 自动发现

启动时 `ExecutorRegistry.discover_and_import_executors()` 扫描 `plugins/executors/` 目录下的所有 `.py` 文件，import 触发 `@register_executor` 装饰器完成注册。

```
src/aiwen/plugins/executors/
├── default/
│   └── concrete.py      # DefaultExecutor
├── conflict/
│   └── concrete.py      # ConflictExecutor
└── your_module/
    └── concrete.py      # 新增的 Executor 放这里即可
```

**无需手动注册**，只要文件在该目录下，启动时自动被发现。

### 1.3 数据库同步

Bootstrap 调用 `sync_all_registries(db)` 将内存中的 Executor 同步到 `executor` 表：

| 阶段 | 动作 |
|------|------|
| Upsert | 为每个注册的 Executor 创建或更新 DB 记录 |
| 孤儿清理 | 代码中已删除的 Executor，DB 中标记 `enabled=False`（软删除） |

### 1.4 运行时实例化

Worker 每次收到任务时创建 **全新的** Executor 实例（不缓存）：

```python
# Worker.prepare_executor() 内部流程：
executor_cls = ExecutorRegistry.get("MyExecutor")       # 1. 取类
config = merge(template_config, app_config)             # 2. 合并配置
config["tool_caller"] = RegistryToolCaller(...)         # 3. 注入依赖
config["tool_provider"] = RegistryToolProvider(...)
executor = executor_cls(config)                         # 4. 实例化
```

### 1.5 事件流式执行

```python
async for event in executor.stream(user_message):
    await event_publisher.publish(event)
```

Run 状态机管理整个流程：

```
PENDING ──start()──→ RUNNING ──complete()──→ COMPLETED
                        │
                   WaitingForTool
                        │
                     WAITING ──resume()──→ RUNNING
                        │
                      FAILED / CANCELLED
```

---

## 2. 创建新 Executor（完整步骤）

### Step 1: 创建文件

```bash
mkdir -p src/aiwen/plugins/executors/my_executor
touch src/aiwen/plugins/executors/my_executor/__init__.py
touch src/aiwen/plugins/executors/my_executor/concrete.py
```

### Step 2: 实现 Executor

```python
# src/aiwen/plugins/executors/my_executor/concrete.py

from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from aiwen.core.interfaces import ToolCaller, ToolProvider
from aiwen.registries import register_executor
from aiwen.core.interfaces import AgentEvent, Executor
from aiwen.schemas.events.event_payloads import UserMessage


@register_executor
class MyExecutor(Executor):
    """自定义 Executor 示例"""

    TEMPLATE: ClassVar[dict[str, Any]] = {
        "template_code": "MyExecutor",
        "template_name": "My Custom Executor",
        "enabled": True,
        "version": 1,
        "config": {
            "model_name": "qwen-plus",
            "max_iterations": 5,
        },
    }

    def __init__(self, config: dict):
        super().__init__(config)
        self.model_name = config.get("model_name", "qwen-plus")
        self.max_iterations = config.get("max_iterations", 5)

        # 依赖注入：Worker 会注入这两个服务
        self.tool_provider: ToolProvider | None = config.get("tool_provider")
        self.tool_caller: ToolCaller | None = config.get("tool_caller")

    async def setup(self) -> None:
        """异步初始化资源（可选）"""
        pass

    async def run(self, user_message: UserMessage | dict) -> dict[str, Any]:
        """同步执行，返回最终结果"""
        message = (
            user_message.message
            if isinstance(user_message, UserMessage)
            else user_message.get("message", "")
        )
        return {"answer": f"Processed: {message}"}

    async def stream(
            self, user_message: UserMessage | dict,
    ) -> AsyncGenerator[AgentEvent, None]:
        """流式执行，yield 事件"""
        self._reset_token_index()

        # 处理恢复执行（HITL 审批后）
        if isinstance(user_message, dict) and user_message.get("_resumed"):
            async for event in self._handle_resume(user_message):
                yield event
            return

        # 正常执行流程
        message = (
            user_message.message
            if isinstance(user_message, UserMessage)
            else user_message.get("message", "")
        )

        # 发射思考过程
        yield self._emit_thinking("Analyzing user input...")

        # 发射流式 token
        for word in message.split():
            yield self._emit_token(word + " ")

        # 如果需要调用工具
        if self.tool_caller:
            tool_name = "get_current_time"
            tool_args = {"timezone": "Asia/Shanghai"}

            yield self._emit_tool_call(tool_name, "call_1", tool_args)
            result = await self.tool_caller.call(tool_name, tool_args)
            yield self._emit_tool_result(tool_name, "call_1", result)

        # 发射最终结果
        yield self._emit_token("", is_final=True)
        yield self._emit_message(f"Done: {message}")
```

### Step 3: 重启服务

```bash
make start-api      # 或 make start-worker
```

启动日志会显示：

```
INFO  Registered component: MyExecutor
INFO  Executor sync: 3 synced, 0 deleted, 0 failed
```

---

## 3. 删除 Executor

**只需删除代码文件**，下次启动时：
- `discover_and_import_executors()` 不再发现该模块
- `_sync_to_database()` 将 DB 中对应记录标记为 `enabled=False`（软删除）

```bash
rm -rf src/aiwen/plugins/executors/my_executor/
```

启动日志：
```
WARNING  Marked as deleted: MyExecutor
```

---

## 4. 更新 Executor

直接修改代码即可。启动时 DB 同步会自动更新：

**更新配置**：修改 `TEMPLATE["config"]`
```python
TEMPLATE = {
    "template_code": "MyExecutor",  # 不要改这个
    "template_name": "My Executor v2",
    "version": 2,                   # 建议递增版本号
    "config": {"model_name": "qwen-max"},
}
```

**更新逻辑**：修改 `run()` / `stream()` 方法。

> **注意**：`template_code` 是主键标识，不要修改。如需重命名，应该删除旧的、创建新的。

---

## 5. 运行原理

### 5.1 事件驱动架构

```
用户发送消息
    │
    ▼
API 创建 Run (status=PENDING)
    │
    ▼
Redis Stream: run_tasks ← {run_id, executor_code, payload}
    │
    ▼
Worker.handle_event()
    ├── 查询 Run + App 配置
    ├── 加载用户自定义工具 (DynamicToolLoader)
    ├── prepare_executor() → 创建 Executor 实例
    └── _execute_run()
         │
         ▼
    executor.stream(user_message)
         │
         ├── yield AgentEvent (THINKING / TOKEN / TOOL_CALL / ...)
         │       │
         │       ▼
         │   EventPublisher → Redis → WebSocket/SSE → 前端
         │
         ├── WaitingForTool → Run.status = WAITING
         │       │
         │       ▼
         │   用户审批 → Redis: run:{id}:resume_approval
         │       │
         │       ▼
         │   Worker 重新消费 → executor.stream({_resumed: true})
         │
         └── 完成 → Run.status = COMPLETED
```

### 5.2 事件类型

Executor 通过 `_emit_*` 方法发射事件：

| 方法 | 事件类型 | 用途 |
|------|----------|------|
| `_emit_token(text)` | `AGENT_TOKEN` | 流式文本 chunk |
| `_emit_message(content)` | `AGENT_MESSAGE` | 最终完整回复 |
| `_emit_thinking(content)` | `AGENT_THINKING` | 推理/思考过程 |
| `_emit_plan_step(n, desc, status)` | `AGENT_PLAN_STEP` | 多步骤进度 |
| `_emit_tool_call(name, id, args)` | `TOOL_CALL` | 工具调用开始 |
| `_emit_tool_result(name, id, result)` | `TOOL_RESULT` | 工具调用成功 |
| `_emit_tool_error(name, id, error)` | `TOOL_ERROR` | 工具调用失败 |
| `_emit_tool_pending(name, id, args)` | `TOOL_PENDING` | 等待人工审批 |
| `_emit_tool_client_request(...)` | `TOOL_CLIENT_REQUEST` | 浏览器端执行 |

### 5.3 依赖注入

Executor **不直接依赖** ToolRegistry 或具体工具模块。Worker 作为组合根（Composition Root）注入两个抽象服务：

```python
# ToolProvider: 提供可用工具列表（用于 bind_tools）
class ToolProvider(Protocol):
    def get_tool_classes(self) -> list[type[BaseTool]]: ...

# ToolCaller: 执行工具调用
class ToolCaller(Protocol):
    async def call(self, tool_name: str, arguments: dict) -> dict: ...
```

这意味着：
- Executor 可以被单独测试（mock 注入）
- 每个 Run 可以有不同的工具集（用户自定义工具）
- 工具加载是按需的，不在 Executor 内硬编码

### 5.4 HITL（人工审批）

当工具需要人工确认时：

```python
# 在 Executor 中配置需要审批的工具
self.approval_tools = config.get("approval_tools", ["sandbox_execution"])

# _process_tool_calls() 中自动判断
if tool_name in self.approval_tools:
    yield self._emit_tool_pending(tool_name, tool_id, args)
    raise WaitingForTool({
        "type": "tool_approval",
        "tool_name": tool_name,
        "tool_id": tool_id,
        "arguments": args,
        "messages": self._serialize_messages(messages),  # 保存完整会话
        "remaining_tool_calls": remaining,
        "executor_code": self.TEMPLATE["template_code"],
    })
```

**流程**：
1. Executor 抛出 `WaitingForTool`，Worker 将对话快照存入 `Run.waiting_for`
2. Run 状态变为 `WAITING`，前端展示审批 UI
3. 用户审批/拒绝 → 写入 Redis `run:{id}:resume_approval`
4. Worker 重新消费，传入 `{_resumed: True, _waiting_info: ..., _approval: ...}`
5. Executor 的 `stream()` 检测到 `_resumed`，从快照恢复会话继续执行

---

## 6. TEMPLATE 字段说明

```python
TEMPLATE: ClassVar[dict[str, Any]] = {
    # 必填
    "template_code": "UniqueId",      # 唯一标识，注册主键，不可变
    "template_name": "Display Name",  # 显示名称

    # 可选
    "enabled": True,                  # 是否启用（默认 True）
    "version": 1,                     # 版本号（默认 1）
    "config": {                       # 默认配置（dict 或 Pydantic model）
        "model_name": "qwen-plus",
        "model_provider": "tongyi",
        "max_iterations": 10,
        "approval_tools": [],
    },
}
```

`config` 可以是 dict 或 Pydantic model（如 `AppConfig`）。运行时 Worker 会将 App 级配置覆盖到 template config 之上。

---

## 7. 两种 Executor 模式对比

### 对话型（DefaultExecutor 模式）

```
LLM调用 → 有工具调用？→ 执行工具 → 结果回传LLM → 循环
                    └→ 无 → 输出最终回复
```

适用于：通用对话 Agent、多轮工具调用、需要 HITL 审批的场景。

### 工作流型（ConflictExecutor 模式）

```
Step 1: 意图解析 → Step 2: SQL生成 → Step 3: 执行查询 → 输出结果
```

适用于：确定性多步骤流程、NL2SQL、报表生成等不需要 LLM 循环的场景。

---

## 8. 数据库模型

```sql
CREATE TABLE executor (
    id          UUID PRIMARY KEY,
    executor_code   VARCHAR UNIQUE NOT NULL,  -- 对应 TEMPLATE["template_code"]
    executor_name   VARCHAR NOT NULL,         -- 对应 TEMPLATE["template_name"]
    enabled     BOOLEAN DEFAULT TRUE,         -- 软删除标记
    config      JSONB,                        -- 配置快照
    version     INTEGER DEFAULT 1,
    created_at  TIMESTAMP,
    updated_at  TIMESTAMP
);
```

CRUD 操作通过 `ExecutorCRUD` 类：

```python
from aiwen.services.executor.executor_template_crud import ExecutorCRUD

crud = ExecutorCRUD(db_session)
await crud.create_executor("code", "name", config={})
await crud.get_executor_by_code("code")
await crud.list_executors(include_disabled=False)
await crud.mark_executor_as_deleted("code")  # 软删除
```

---

## 9. 快速参考

| 操作 | 方法 |
|------|------|
| 新增 Executor | 在 `plugins/executors/` 下创建模块，用 `@register_executor` 装饰 |
| 删除 Executor | 删除代码文件，重启后自动软删除 |
| 更新 Executor | 修改代码，重启后自动同步 |
| 获取 Executor 类 | `ExecutorRegistry.get("template_code")` |
| 列出所有 Executor | `ExecutorRegistry.list()` |
| 手动注册（含DB） | `registry.register_with_db(code, name, cls, db)` |
| 数据库同步 | `await registry.sync_to_database(db)` |