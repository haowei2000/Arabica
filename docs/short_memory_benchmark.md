# Short Memory 回归 Benchmark

**Status:** Draft — 2026-07-20
**Owner:** haowei
**Related:** `docs/short_memory_design.md` (三原则), `benchmarks/core/` (现有度量基础设施)

---

## 0. 目的

为 short memory 改动提供**无需真实 LLM**的回归验证。每次改动 GC 参数、去重策略、截断阈值后，跑这个 benchmark，对比三维指标 delta。

**不是**一个新的 LLM benchmark——它跑在 **event log 上的纯函数**，零 LLM 成本，CI 秒级运行。

---

## 1. 度量合约：三维指标

| 维度 | 指标 | 定义 | 理想方向 |
|---|---|---|---|
| **P1 缓存命中率** | `head_stability_bytes` | system prompt 部分的字节数变化量（跨相邻 LLM 调用） | ↓ 越小越好 |
| | `body_total_bytes` | 所有 replayed message 的总字节数 | ↓ 越小越好 |
| | `cache_hit_proxy` | `1 - (Δprompt_tokens / total_prompt_tokens)` | ↑ 越高越好 |
| **P2 避免重复操作** | `dedup_ratio` | 被折叠的重复操作 / 总 tool 操作数 | ↑ 越高越好 |
| | `replayed_tool_result_chars` | 回放中 tool result 的总字符数 | ↓ 越小越好 |
| | `unique_tool_keys` | 去重后的 (tool_name, args_hash) 集合大小 | ↓ 越小越好 |
| **P3 GC 释放** | `archive_yield_events` | GC 标记为 archived 的事件数 | ↑ 释放越多热区越小 |
| | `archive_yield_tokens` | 释放事件携带的 input_tokens + output_tokens 之和 | ↑ 释放越多 |
| | `hot_events_remaining` | GC 后热区 (`is_archived=false`) 事件数 | ↓ 越小越好 |
| | `replay_after_gc_bytes` | GC 一轮后 body_total_bytes | ↓ 越小越好 |

### 综合分数

```
short_memory_score = w1 * cache_hit_proxy
                   + w2 * dedup_ratio
                   + w3 * (1 - normalized_replay_bytes)

默认权重: w1=0.4, w2=0.3, w3=0.3   (可通过 CLI 覆盖)
```

权重反映优先级：缓存命中率 > 去重效率 > 绝对长度压缩。

---

## 2. 输入：事件日志 fixture

### 2.1 真实回放 fixture

```bash
# 从真实 session 导出事件日志（脱敏后存入 repo）
python -c "
from benchmarks.short_memory.dataset import export_session_events
export_session_events(run_id='...', out='benchmarks/short_memory/fixtures/session_42_events.jsonl')
"

# fixture 格式: 一行一个 event dict，与 Event 模型一致
# {"sequence": 1, "event_type": "user_message", "payload": {...}, ...}
# {"sequence": 2, "event_type": "agent_message", "payload": {...}, ...}
```

### 2.2 合成 fixture（人工构造高冗余 case）

```python
# dataset.py 里的合成器：构造已知冗余度的事件日志
from benchmarks.short_memory.dataset import generate_redundant_log

events = generate_redundant_log(
    n_turns=50,
    repeat_ratio=0.4,      # 40% 的操作是重复的
    repeated_keys=[("read_context", "/knowledge/api_spec")],
)
```

合成 fixture 的优势：**已知 ground truth**（精确知道有多少重复、预期去重率多少），用于验证 dedup 逻辑正确性。

### 2.3 最小 smoke fixture

```bash
# 5 个事件，1 个重复 tool result — 用于 CI 快速 sanity check
benchmarks/short_memory/fixtures/smoke.jsonl
```

---

## 3. 架构

```
benchmarks/short_memory/
├── __init__.py
├── dataset.py            # fixture 加载 + 合成事件生成器
│   ├── load_fixture(path) -> list[Event]
│   ├── generate_redundant_log(n_turns, repeat_ratio, ...) -> list[Event]
│   └── export_session_events(run_id, out)   # 从真实 session 导出
├── metrics.py            # 三维指标计算
│   ├── compute_head_stability(events) -> HeadStabilityResult
│   ├── compute_dedup(events, dedup_fn) -> DedupResult
│   ├── compute_gc_yield(events, gc_strategy) -> GcYieldResult
│   └── compute_short_memory_score(metrics, weights) -> float
├── replay.py             # 封装 _events_to_messages 调用 + 测量
│   └── replay_with_metrics(events) -> ReplayResult
├── fixtures/
│   ├── smoke.jsonl                    # 5 事件 sanity
│   ├── synthetic_redundant.jsonl     # 50 turn, 40% 重复
│   └── session_42_events.jsonl       # 真实回放 (gitignore 大号)
├── tests/
│   ├── test_short_memory_metrics.py  # 单元：指标计算正确性
│   ├── test_short_memory_regression.py  # 回归：改动前后 delta
│   └── test_short_memory_dedup.py    # dedup 逻辑 oracle（必须 dedup_ratio=1.0）
└── README.md
```

---

## 4. 与现有 benchmark 基础设施的关系

**复用** `benchmarks/core/` 的以下构件：

- `BenchmarkReport` / `CostLedger` 作为输出格式（`CostLedger.steps` 承载 step 数，`tokens_prompt` 承载 body 大小）
- `Scorer` 签名：`(reference, response) -> float` — 此处 reference 是 fixture 标注的预期去重率，response 是实际 dedup 结果
- `aggregate()` 聚合多 case 结果
- `_bucketed_accuracy()` — 复用为 "accuracy_by_body_size_bucket"

**不复用**的部分：

- `AgentProtocol`：不需要——这个 benchmark 调的是 `_events_to_messages()` 纯函数，不走 LLM
- `BenchmarkCase`：简化为 `(name, events, expected_dedup_ratio)` 三元组

### 输出映射

| 三维指标 | → BenchmarkReport 字段 |
|---|---|
| cache_hit_proxy | `diagnostic_summary.kv_cache.prompt_token_hit_rate` |
| dedup_ratio | `diagnostic_summary.short_memory.dedup_ratio` |
| body_total_bytes | `mean_cost.tokens_prompt` (近似) |
| archive_yield_events | `diagnostic_summary.short_memory.archive_yield_events` |
| short_memory_score | `overall_score` |

---

## 5. 运行方式

```bash
# 跑全部 fixture
uv run pytest benchmarks/short_memory/ -v

# CLI 对比（改动前后）
uv run benchmark-short-memory --fixture session_42 --weights 0.4,0.3,0.3
# 输出:
#   head_stability_bytes:  0
#   body_total_bytes:      48230 → 31800  (Δ -34.1%)
#   dedup_ratio:           0.00 → 0.31
#   cache_hit_proxy:       0.62 → 0.71     (Δ +9pp)
#   archive_yield_events:  0 → 47
#   short_memory_score:    0.41 → 0.68     (Δ +0.27)

# 生成 paper 表格行
uv run benchmark-short-memory --fixture session_42 --format latex
# → 0.41 ± 0.03 → 0.68 ± 0.02  (+0.27)
```

### CI 集成

```yaml
# .github/workflows/ci.yml 新增步骤
- name: Short Memory Regression
  run: |
    uv run pytest benchmarks/short_memory/ -m unit --tb=short
    # 如果 synthetic_redundant 的 dedup_ratio < 阈值 → 失败
    uv run benchmark-short-memory --fixture synthetic_redundant \
      --assert-dedup-above 0.35 --assert-score-above 0.50
```

---

## 6. 度量代理方案（无需加 event 字段）

event 表目前只有 `input_tokens` / `output_tokens`，**没有** `cache_read_tokens` / `cache_creation_tokens`。

### 方案：prompt token delta 作为 cache hit 代理

```
cache_hit_proxy = 1 - (prompt_tokens[turn_N] - prompt_tokens[turn_0]) / prompt_tokens[turn_N]
```

原理：如果 head 完全不变，新增的 prompt token **纯粹来自 body 增长**。body 增长越慢，cache hit proxy 越高。

- 优点：零-schema 改动，立即可用
- 缺点：不是真实的 KV cache 物理命中率（服务商内部实现）
- 当方向：event 表未来加 `cache_read_tokens` 字段后，可切换为精确度量，benchmark 接口不变

### Body 长度度量

直接用 `_events_to_messages()` 输出的 `ChatMessage` 总字符数：

```python
replay_msgs = _events_to_messages(events)
body_total_bytes = sum(len(m.content or "") for m in replay_msgs if m.role in ("user", "tool"))
```

这比 token 数更稳定（不依赖 tokenizer），且与 prompt token 高度线性相关。

---

## 7. 改动记录格式（用于 paper ablation）

每次改动 benchmark 自动生成一条记录：

```markdown
## 2026-07-20 — Tool result dedup v1

- 改动: `_events_to_messages()` 增加 (tool_name, args_hash) 最近结果缓存
- 重复命中替换为 `<cached from turn {N}, sha256={digest}, re-read: read_context("{path}")>`
- fixture: session_42 (真实), synthetic_redundant (合成)

| 指标 | before | after | delta |
|---|---:|---:|---:|
| cache_hit_proxy | 0.62 | 0.71 | +0.09 |
| dedup_ratio | 0.00 | 0.31 | +0.31 |
| body_total_bytes | 48230 | 31800 | -34.1% |
| archive_yield_events | 0 | 47 | +47 |
| short_memory_score | 0.41 | 0.68 | +0.27 |

结论: P2 杠杆生效，三方受益。body 缩短 34% 是主要 delta 来源。
```

这份记录直接进入 `paper/sections/results.tex` 的 ablation 表。

---

## 8. 实现优先级

1. **P0 — 现在可以写：**
   - `dataset.py`：fixture 加载 + 合成器（纯 Python，无新依赖）
   - `metrics.py`：三维指标计算（纯函数）
   - `tests/test_short_memory_metrics.py`：oracle 验证（synthetic fixture 的 dedup_ratio 必须 = 预期）

2. **P1 — P0 跑通后：**
   - `replay.py`：封装 `_events_to_messages` + 自动度量
   - CLI 入口 `benchmark-short-memory`
   - 从真实 session 导出第一条 real fixture

3. **P2 — 有了真实数据后：**
   - CI 集成
   - paper ablation 表格生成
   - event 表加 `cache_read_tokens` 字段后切换精确度量
