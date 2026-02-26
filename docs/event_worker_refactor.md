# Event Worker 重构文档

## 架构变更

### 旧架构 (Before)
```
Event → Worker.handle_event()
          ├→ _handle_user_message() [业务逻辑]
          ├→ _handle_user_feedback() [业务逻辑]
          ├→ _handle_tool_call() [业务逻辑]
          ├→ _handle_tool_client_request() [业务逻辑]
          ├→ _handle_run_cancellation() [业务逻辑]
          ├→ _handle_task_event() [业务逻辑]
          └→ _handle_artifact_event() [业务逻辑]
```

**问题**：
- Worker 包含大量业务逻辑
- 难以扩展和维护
- Executor 和 Worker 职责不清晰

### 新架构 (After)
```
Event → Worker.handle_event()
          ├→ _should_skip_event() [过滤]
          ├→ _get_or_create_executor() [路由]
          └→ executor.process_event() [转发]
                └→ Executor 内部处理所有业务逻辑
```

**优势**：
- Worker 只负责过滤和路由
- 所有业务逻辑在 Executor 中
- 职责清晰，易于扩展

## 工作流程

### 1. 事件过滤 (`_should_skip_event`)

跳过以下类型的事件（这些是输出事件，不需要处理）：
- `agent.*` - Agent 输出事件（token, message, thinking, plan.step, heartbeat）
- `tool.result`, `tool.error` - 工具执行结果（由 worker 发布，不消费）
- `run.*` - Run 生命周期事件（由状态机发布）

### 2. Executor 获取/创建 (`_get_or_create_executor`)

- 检查 run_id 是否存在对应的 executor
- 如果是 `user.message` 事件且 executor 不存在，则创建新的
- 创建过程包括：
  - 加载 run 数据
  - 加载用户自定义工具
  - 准备 executor 配置
  - 实例化 executor
  - 附加到运行时管理器
  - 转换 run 状态到 running

### 3. 事件转发

将 Event 转换为 AgentEvent 并调用：
```python
await executor.process_event(agent_event)
```

## 事件处理责任分配

| 事件类型 | 处理者 | 说明 |
|---------|--------|------|
| `user.message` | Executor | 触发 Agent 执行 |
| `user.feedback` | Executor | 处理用户反馈 |
| `tool.call` | Executor | 执行工具调用 |
| `tool.pending` | Executor | 处理工具审批 |
| `tool.client.request` | Executor | 处理客户端工具请求 |
| `run.cancelled` | Executor | 清理 run 资源 |
| `task.*` | Executor | 任务生命周期管理 |
| `artifact.*` | Executor | Artifact 生命周期管理 |
| `agent.*` (output) | 跳过 | 由 Executor 发布，Worker 不消费 |
| `tool.result/error` | 跳过 | 由 Worker 发布，Executor 通过 resume 读取 |
| `run.*` (lifecycle) | 跳过 | 由状态机发布 |

## 迁移指南

### Executor 实现示例

```python
class MyExecutor(Executor):
    async def _process_user_message(self, payload: dict[str, Any]) -> None:
        """处理用户消息 - 启动 Agent 执行"""
        message = payload.get("message")
        # 执行 Agent 逻辑...

    async def _process_tool_call(self, payload: dict[str, Any]) -> None:
        """处理工具调用"""
        tool_name = payload.get("tool_name")
        tool_id = payload.get("tool_id")
        arguments = payload.get("arguments", {})

        # 调用工具
        tool_caller = self.config.get("tool_caller")
        result = await tool_caller.execute(tool_name, arguments)

        # 发布结果
        yield self._emit_tool_result(tool_name, tool_id, result)

    async def _process_run_cancelled(self, payload: dict[str, Any]) -> None:
        """处理 run 取消 - 清理资源"""
        # 清理逻辑...
```

## 代码清理

### 已删除的方法（旧业务逻辑，现在在 Executor 中）
- `_handle_user_message()` - 移到 Executor._process_user_message()
- `_handle_user_feedback()` - 移到 Executor._process_user_feedback()
- `_handle_tool_call()` - 移到 Executor._process_tool_call()
- `_handle_tool_client_request()` - 移到 Executor._process_tool_client_request()
- `_handle_run_cancellation()` - 移到 Executor._process_run_cancelled()
- `_handle_task_event()` - 移到 Executor._process_task_*()
- `_handle_artifact_event()` - 移到 Executor._process_artifact_*()

### 保留的方法（辅助功能）
- `prepare_executor()` - 准备 executor 配置
- `_fetch_run_data()` - 获取 run 数据
- `_parse_redis_event()` - 解析 Redis 事件
- `_ensure_consumer_group()` - 确保消费者组存在

## 测试要点

1. ✅ 验证事件过滤正确（输出事件被跳过）
2. ✅ 验证 executor 创建和附加
3. ✅ 验证事件正确转发到 executor
4. ✅ 验证错误处理（executor 不存在、创建失败等）
5. ✅ 验证 run 状态转换正确

## 兼容性

- ✅ 向后兼容：现有的 SimpleExecutor 等需要实现相应的 `_process_*` 方法
- ✅ EventPublisher 保持不变
- ✅ RunStateMachine 保持不变
- ✅ Redis Stream 消费机制保持不变

## 注意事项

1. **Executor 必须实现事件处理方法**：所有 EventType 都有对应的 `_process_*` 方法（默认为空操作）
2. **工具执行**：Executor 通过注入的 `tool_caller` 执行工具，不再由 Worker 处理
3. **状态转换**：Worker 只负责初始的 pending → running 转换，其他转换由 Executor 触发
4. **错误处理**：Worker 捕获错误并调用 `state_machine.fail()`，Executor 内部错误由自己处理

## 性能影响

- ✅ 减少 Worker 的代码复杂度
- ✅ 更好的关注点分离
- ✅ 更容易进行单元测试
- ✅ 略微增加方法调用开销（process_event 多一层）
