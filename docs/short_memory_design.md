# Short Memory 设计：三原则平衡

> Historical document: Python services, memory benchmark runners and Harbor
> orchestration were removed at the Rust-only cutover. References to those
> components describe the original design or campaign, not current runnable
> interfaces. See the repository benchmark guide for maintained entry points.

**Status:** Draft — 2026-07-20
**Owner:** haowei
**Related:** `docs/runtime_core_architecture.md` (v2 Rust core), `benchmarks/short_memory/` (回归验证)

> **Rust v2 alignment (2026-07-26):** `structure-session` owns the append-only
> per-Session event history and captures inherited history at a fork boundary.
> `structure-runtime::ShortMemoryProjector` now applies deterministic
> event-count TTL, five-class retention, relation-aware decay, pinning, a
> recency floor, stable semantic batching, deterministic key-budget admission,
> and `LOAD_ALL` / `LOAD_KEY` / `NO_LOAD` materialisation. Workspace
> Long Memory remains separate and reaches the model through a distinct
> `long_memory` field. Short Memory is not writable through `context.*`.

### Rust v2 materialisation contract

The Rust implementation returns four replayable products from the same
immutable event slice:

1. Per-event visibility decisions containing memory class, relation key,
   accumulated decay, configured TTL, pin state, and recency-floor protection.
2. Stable batches containing `context_key`, kind, sequence span, event count,
   estimated token count, bounded `key_content`, and load state.
3. The ordered provider-neutral short-memory entries used for the next model
   request.
4. A key-admission summary containing the serialized policy, candidate/admitted
   counts, rejection count, and admitted key-content bytes.

`LOAD_ALL` preserves typed user/assistant messages and typed
`ToolCall`/`ToolResult` pairs. `LOAD_KEY` emits a deterministic batch index
without an LLM summarisation call. `NO_LOAD` omits the batch from the prompt
without deleting or rewriting its source events. Promotion or archival into
Long Memory remains an explicit operation outside the collector.

An optional hard budget limits historical keys by count and total UTF-8 key
content bytes. Admission is deterministic: memory-class evidence value, batch
kind, then recency in the Session-supplied lineage order. Every candidate keeps
an explainable rank, candidate size, and admission/rejection reason. The
default is unbounded so enabling the mechanism requires an explicit policy.

Detailed Protocol events are projected into five Runtime-only retention
classes: `Anchor`, `Working`, `Recovery`, `Transient`, and `Control`. This
keeps the wire audit vocabulary independent from GC policy. Decay rules may
also require a matching relation key, so `tool.result(call-7)` accelerates
only `tool.call(call-7)`, not every older tool call.

---

## 0. 定义

Short memory 不是一个独立存储。它是 **session 运行日志（append-only event log）经过筛选函数后、喂给 LLM 的瞬时快照**。

```
事件日志 (全量, 持久)
     │
     ▼  筛选函数: _events_to_messages(events) → ChatMessage[]
     │
     ├── 类型映射   (只保留 5 种事件 → role=user/assistant/tool)
     ├── 载荷截断   (tool args >1200字 / tool result >2400字 → 截断 + sha256)
     └── 结构修复   (孤立 tool 消息降级、未完成 tool_calls 剥离)
     │
     ▼
LLM 看到的 short memory (一次性 prompt)
```

真相源只有一个：事件日志。Short memory 是它的**派生视图**。

---

## 1. 三原则

Short memory 的设计受三个互相竞争的目标约束：

| 原则 | 含义 | 度量 |
|---|---|---|
| **P1. Head Cache 命中率** | prompt 头部的 system prompt + tool schema + workspace context 尽量稳定，让 LLM 服务的 prefix cache 命中 | `head_stability` = system 部分长度变化量；代理指标 `cache_hit_proxy = 1 - Δprompt / total_prompt` |
| **P2. 避免重复操作** | 把重要且频繁用到的操作结果放进上下文，避免同一工具/路径被反复调用和反复回放 | `dedup_ratio` = 被折叠的重复操作 / 总操作数 |
| **P3. GC 释放到持久存储** | 热区事件增长到阈值后，打包归档到 workspace context 表（冷区），标记 `is_archived=true`，从热路径移除 | `archive_yield` = 释放的 token 数；归档后通过 `read_context("/archive/...")` 可回查 |

### 三方博弈关系

```
        P1 缓存命中率 ←──────→ P2 避免重复操作
       (head 越稳定越好)       (body 越精简越好)
              ↑       ╲    ╱       ↑
               ╲       ╳        ╱
                ╲     ╱ ╲      ╱
                 ╲   ╱   ╲    ╱
                  P3 GC 释放
            (热区 ↔ 冷区流动)
```

- **P1 vs P2**：head 稳定要求 context 不频繁更新；但新操作结果要写进 context 才能避免重复 → 新 context 写入频率直接决定 head cache 失效率。
- **P2 vs P3**：去重减少 body 增长 → 间接减少 GC 压力；GC 把旧事件归档 → 减少可去重的候选池。
- **P1 vs P3**：GC 触发 `invalidate_workspace_context_cache` → 可能改变 head → cache 失效。需要 GC 节流或批量合并。

**支点：P2（去重）是唯一一个改动能同时让三方受益的杠杆。** 去重成功 → body 缩短（P2↑）→ GC 频率降低（P3↑）→ head 更稳定（P1↑）。

---

## 2. 现状对齐

### P1 — Head Cache：部分对齐

| 机制 | 代码位置 | 效果 |
|---|---|---|
| `_runtime_context_message()` 把动态 ID 抽到 messages 尾部 | `executor/default/concrete.py` | head 不变 → prefix cache 命中 |
| `_insert_runtime_context()` 从后往前找 user message 再插入 | 同上 | 保护 head + body 完整性 |
| `_raw_events_cache` 预取事件 | 同上 | 避免 run 内重复查 DB |

**缺口：** workspace context 更新无节流。新 context 写入频率直接决定 head cache 失效率。

### P2 — 避免重复操作：严重不足

| 机制 | 效果 |
|---|---|
| `_merge_tools_info()` tool schema 按名去重 | 仅避免 tool 定义重复，不涉及操作结果 |
| `_extract_context_tool_schemas()` 从事件提取 schema | 避免重复查询 tool schema |

**缺口：tool result 零去重。** 同一 `read_context` 路径被读 N 次 → N 次结果全部回放，既不合并也不提示"已在 turn X 读过"。这是 step 数和 context 长度同时膨胀的直接原因。

### P3 — GC 释放：完整实现

| 机制 | 代码位置 | 效果 |
|---|---|---|
| `EventCountTTLStrategy` 按事件类型衰减 | `services/events/event_gc.py` | 默认 6 个同类事件后衰减 |
| `AggressiveActiveMemoryGCStrategy` 激进策略 | 同上 | token/heartbeat 直接 ttl=0 |
| `_create_archive_contexts()` 打包写入 workspace context | `services/events/event_archive.py` | 归档到 `/archive/{scope}/{id}/...`，保留 glance/overview/detail 三级 |
| `_mark_archived_bulk()` 标记 `is_archived=true` | 同上 | 软删除，审计行保留 |
| `invalidate_workspace_context_cache()` | 归档后触发 | 热区 cache 失效 |

**缺口：** GC 触发只有 `manual_event_gc`，缺周期性自动触发。GC 动力是"事件数量/字符数"而非"语义冗余度"。

---

## 3. 改动杠杆与预期影响

| 改动 | P1 缓存命中 | P2 去重/step | P3 GC 压力 |
|---|---|---|---|
| **tool result dedup**：回放时对 `(tool_name, args)` 最近结果缓存，重复命中替换为 `<cached from turn N, sha256=..., read_context 回查路径>` | ↑ body 缩短 → head 占比提升 | ↑↑ 直接消除重复回放 | ↓ 可 GC 的事件减少 |
| **context 写入节流**：合并短时间内的 context 更新，批量写入 | ↑ head 变化频率降低 | — | — |
| **GC 自动触发**：按 body 增长速率定时 GC 而非手动 | ↓ 触发时 cache 失效 | — | ↓ 热区大小受控 |
| **GC 后 head 保护**：归档后延迟 invalidate，或只 invalidate 变化的 context 节点 | ↓ 减少无效 cache 失效 | — | — |
| **replay 时折叠旧 turn**：超过 N 轮的 tool result 只保留摘要 | ↑ body 缩短 | — | ↓ 间接减少 GC 候选 |

---

## 4. 代码位置速查

```
筛选函数 (short memory 核心):
  src/structure/plugins/executors/default/concrete.py
    ├── _events_to_messages()        # 事件 → ChatMessage 转换 (类型映射)
    ├── _compact_replay_payload()    # 载荷截断 (1200/2400 阈值)
    ├── _strip_orphaned_tool_messages()  # 结构修复
    ├── _runtime_context_message()   # 动态 ID 抽尾 (P1)
    └── _insert_runtime_context()    # head 保护插入 (P1)

GC 释放 (P3):
  src/structure/services/events/
    ├── event_gc.py                  # TTL 策略 + 衰减规则
    └── event_archive.py             # 归档到 workspace context

度量缺口:
  src/structure/models/events/event.py
    ├── input_tokens / output_tokens  # 有
    └── cache_read_tokens / cache_creation_tokens  # 缺 — 无法直接采集 cache hit
```

---

## 5. 已实现状态 (2026-07-20)

| 原则 | 状态 | 实现位置 | 验证 |
|---|---|---|---|
| P1 Head Cache — 头稳定 | 部分 | `executor/default/concrete.py` `_runtime_context_message` / `_insert_runtime_context` | 现有 executor 测试 |
| P2 避免重复操作 — tool result dedup | **已初版实现** | `_events_to_messages(dedup_tool_results=True)` + `_tool_result_re_query_path()` | `benchmarks/short_memory/` oracle 精确命中 0.35/0.35 |
| P3 GC 释放到持久存储 | 已有（缺自动触发） | `services/events/event_gc.py` + `event_archive.py` | 已有事件日志测试 |

### Dedup v1 设计要点

- 去重键: `(tool_name, sha256(result_json)[:16])` — 稳定且对内容敏感
- 占位符格式: `<dedup: first seen turn N, sha256=<digest>, re-read: read_context("path")>` — **可逆**（LLM 可以根据提示回查）
- 回查路径仅限 `read_context`（从 payload.result.path 或 payload.arguments.path 恢复），未来扩展到其他 idempotent 工具
- 默认开启（`dedup_tool_results=True`），可通过 executor config `{"dedup_tool_results": false}` 关闭

## 6. 设计约束

1. **事件日志是唯一的真相源。** 任何 short memory 机制不得修改已写入的事件（append-only）。
2. **筛选函数是纯函数。** `list[Event] → list[ChatMessage]`，无副作用，不写存储，只在内存转换。
3. **归档不是删除。** GC 后事件标记 `is_archived=true`，审计行保留，内容打包进 workspace context 可回查。
4. **去重必须可逆。** 折叠的 tool result 必须保留 sha256 + 回查路径，LLM 可在需要时通过 `read_context` 拿回原文。
5. **度量先于优化。** 任何改动必须通过 `benchmarks/short_memory/` 的三维回归测试，不允许无数据改动。
