# Agent Workspace 架构差距分析

## 目标架构 vs 当前实现

### 1️⃣ Workspace 作为一等公民

| 目标                 | 当前状态                     | 差距等级  |
|--------------------|--------------------------|-------|
| Workspace 是唯一上下文容器 | ❌ 使用 Conversation 作为容器   | 🔴 重大 |
| 可分享、可多人协作          | ❌ 无分享/协作机制               | 🔴 重大 |
| 可回放、可审计            | ⚠️ 有 Message 记录，但无完整事件日志 | 🟡 中等 |
| 包含工具执行状态           | ❌ 无工具状态跟踪                | 🔴 重大 |
| 所有事件时间线            | ❌ 无统一事件系统                | 🔴 重大 |

**需要**：创建 `Workspace` 模型，替代 `Conversation` 作为顶层容器

---

### 2️⃣ Run 作为核心执行单元

| 目标                  | 当前状态                                      | 差距等级  |
|---------------------|-------------------------------------------|-------|
| Run 有完整状态机          | ⚠️ AgentTask 有 4 种状态，缺少 waiting/cancelled | 🟡 中等 |
| Run 可被暂停/恢复         | ❌ 无暂停/恢复机制                                | 🔴 重大 |
| Run 可被工具回调触发        | ❌ 工具无法触发 Run 恢复                           | 🔴 重大 |
| Run 可并发执行           | ⚠️ 无冲突控制机制                                | 🟡 中等 |
| 多 Run 在一个 Workspace | ❌ 当前 1:1 (Conversation:Task)              | 🔴 重大 |

**需要**：重构 `AgentTask` 为 `Run` 模型，添加状态机和暂停/恢复机制

---

### 3️⃣ 双通道流式

| 目标                       | 当前状态                | 差距等级  |
|--------------------------|---------------------|-------|
| Chat 流式 (SSE)            | ✅ 已实现               | 🟢 完成 |
| Workspace 事件 (WebSocket) | ❌ 无 WebSocket       | 🔴 重大 |
| 多人协作感知                   | ❌ 无实现               | 🔴 重大 |
| 刷新后可重建视图                 | ⚠️ 可加载历史消息，但无完整状态重建 | 🟡 中等 |

**需要**：添加 WebSocket 层用于 Workspace 事件广播

---

### 4️⃣ Event-first 架构

| 目标              | 当前状态                     | 差距等级  |
|-----------------|--------------------------|-------|
| 所有行为 → Event    | ❌ 仅存储最终结果                | 🔴 重大 |
| Event 持久化 (PG)  | ❌ 无 Event 表              | 🔴 重大 |
| Event 广播 (WS)   | ❌ 无 WebSocket            | 🔴 重大 |
| Event 流式 (Chat) | ⚠️ 仅 chunk/success/error | 🟡 中等 |
| UI 是事件投影        | ❌ UI 直接查询数据              | 🔴 重大 |

**需要**：设计统一的 Event 系统，支持 Event Sourcing

---

### 5️⃣ 多人协作 & 冲突处理

| 目标                | 当前状态    | 差距等级  |
|-------------------|---------|-------|
| 多用户同时进入 Workspace | ❌ 无协作支持 | 🔴 重大 |
| Run 级隔离           | ❌ 无隔离机制 | 🔴 重大 |
| Workspace 级并发可见   | ❌ 无实现   | 🔴 重大 |
| 软锁定资源             | ❌ 无锁机制  | 🔴 重大 |

**需要**：添加协作层，包括 Presence、Lock、冲突检测

---

### 6️⃣ 工具系统

| 目标              | 当前状态             | 差距等级  |
|-----------------|------------------|-------|
| 工具支持 pending 状态 | ❌ 同步执行           | 🔴 重大 |
| 工具回调恢复 Run      | ❌ 无回调机制          | 🔴 重大 |
| 工具执行可流式上报       | ❌ 无中间状态上报        | 🔴 重大 |
| 工具执行可被前端观察      | ❌ 前端无感知          | 🔴 重大 |
| 工具原子化 + 可组合     | ⚠️ MCP 集成，但无分层设计 | 🟡 中等 |

**需要**：重构工具系统为状态机，支持异步执行和事件上报

---

### 7️⃣ 客户端文件系统交互

| 目标                       | 当前状态                 | 差距等级  |
|--------------------------|----------------------|-------|
| 不上传整个文件夹                 | ⚠️ 当前是服务端存储 (S3/OSS) | 🟡 中等 |
| Agent 可读/写/创建文件          | ⚠️ 通过服务端 API         | 🟡 中等 |
| 工具执行位置可变 (server/client) | ❌ 仅服务端执行             | 🔴 重大 |
| 用户确认 → 客户端执行 → 结果回传      | ❌ 无此流程               | 🔴 重大 |

**需要**：设计 Client-Server 混合工具执行协议

---

### 8️⃣ 上下文渐进式披露

| 目标                       | 当前状态    | 差距等级  |
|--------------------------|---------|-------|
| 上下文分层 (System/Mode/Tool) | ❌ 扁平结构  | 🔴 重大 |
| 不同角色看不同层级                | ❌ 无角色区分 | 🔴 重大 |
| Plan/推理过程可选择性披露          | ❌ 无披露控制 | 🔴 重大 |

**需要**：设计上下文分层模型和访问控制

---

### 9️⃣ Prompt 作为系统资产

| 目标                           | 当前状态                          | 差距等级  |
|------------------------------|-------------------------------|-------|
| Prompt 分层 (System/Mode/Tool) | ⚠️ AgentTemplate 有 config     | 🟡 中等 |
| Prompt 可版本化                  | ⚠️ AgentTemplate 有 version 字段 | 🟡 中等 |
| Prompt 可审计                   | ❌ 无审计日志                       | 🟡 中等 |
| React/Plan 模式 Prompt         | ❌ 无模式区分                       | 🔴 重大 |

**需要**：扩展 Prompt 管理系统，支持分层和版本控制

---

## 📈 差距统计

| 等级    | 数量 | 说明        |
|-------|----|-----------|
| 🔴 重大 | 25 | 需要新建或重构   |
| 🟡 中等 | 10 | 需要扩展或增强   |
| 🟢 完成 | 1  | SSE 流式已实现 |

---

## 🎯 优先级建议

### Phase 1: 核心重构 (基础)

1. **Event 系统** - 所有后续功能的基础
2. **Workspace 模型** - 替代 Conversation
3. **Run 状态机** - 重构 AgentTask

### Phase 2: 实时通信

4. **WebSocket 层** - Workspace 事件广播
5. **双通道架构** - SSE + WebSocket 协同

### Phase 3: 工具系统

6. **工具状态机** - pending/running/done/failed
7. **工具事件上报** - 流式进度
8. **Client-Server 混合执行**

### Phase 4: 协作特性

9. **多人协作** - Presence、Lock
10. **冲突控制** - 可解释的并发

### Phase 5: 高级特性

11. **渐进式上下文披露**
12. **Prompt 资产管理**
13. **回放与审计**

---

## 🏗️ 建议的新模型设计

```
Workspace (新)
├── id, name, created_by
├── settings (JSON)
├── members[] (协作)
└── runs[]

Run (重构自 AgentTask)
├── id, workspace_id
├── status (pending/running/waiting/finished/cancelled)
├── trigger_type (user/tool_callback/agent)
├── parent_run_id (嵌套执行)
└── events[]

Event (新)
├── id, workspace_id, run_id
├── event_type (enum)
├── payload (JSON)
├── timestamp
├── visibility_level (public/internal/debug)
└── actor_type (user/agent/tool/system)

ToolExecution (新)
├── id, run_id, tool_name
├── status (pending/running/done/failed)
├── input, output
├── execution_location (server/client)
└── events[]

WorkspaceMember (新)
├── workspace_id, user_id
├── role (owner/editor/viewer)
├── joined_at
└── presence_status
```
