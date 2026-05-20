# Research Radar Mode

Research Radar is a scheduled discovery workflow for tracking external
features, papers, benchmarks, and implementation patterns that are relevant to
Structure. It is intentionally narrow: the goal is to produce a short,
actionable record every day, not a broad literature survey.

## Scope

The daily scan should focus on material that can influence one of these areas:

- Agent orchestration: executor loops, multi-agent coordination, tool routing,
  human-in-the-loop control, and failure recovery.
- Event-sourced agent systems: immutable logs, replay, auditability, state
  reconstruction, Redis stream or queue based workers.
- Context and memory: path-addressable context, long-term memory, retrieval
  strategies, compression, summarization, and workspace knowledge stores.
- Tool use and MCP: tool schema design, tool selection, sandboxed execution,
  protocol compatibility, and tool-result streaming.
- Benchmarks: LongMemEval, LoCoMo, HELMET, tau-bench, RULER, SWE-bench,
  WebArena, OSWorld, WorkArena, AppWorld, GAIA, AgentBoard, BFCL, LOFT, and
  comparable agent or memory evaluations.
- Production backend concerns: FastAPI async services, distributed workers,
  streaming APIs, observability, security, and cost or latency control for
  agent platforms.

## Daily Workflow

1. Read the local project profile:
   - `README.md`
   - `docs/roadmap.md`
   - `benchmarks/README.md`
   - recent files under `benchmarks/`, `src/structure/services/`,
     `src/structure/core/`, and `src/structure/plugins/`
2. Search for new or recently discussed material from the last 7 days.
3. Use a paper-first source strategy:
   - first search papers and preprints from arXiv, OpenReview, ACL Anthology,
     ACM, IEEE, USENIX, NeurIPS, ICLR, ICML, COLM, EMNLP, NAACL, and related
     venue proceedings
   - then check benchmark pages, datasets, official docs, release notes, and
     source repositories that are linked from or directly support those papers
   - use blog posts, vendor posts, newsletters, or social posts only when they
     point to a primary paper/benchmark source or contain reproducible
     implementation details and numbers
   - if non-paper sources are promoted, explain why no stronger paper source was
     available or why the source is still operationally important
4. Score candidates using the relevance rubric below.
5. Record only the top 3-8 useful findings.
6. For every recorded item, compare it against Structure's current design and
   benchmark harness before proposing actions.
7. Extract concrete follow-up tasks when a finding maps cleanly to Structure.
8. Send the daily result as a readable bilingual HTML email report.

## Query Bands

Rotate these query bands rather than running one generic search:

| Band | Example queries |
| --- | --- |
| Agent runtime | `agent orchestration framework tool calling streaming benchmark`, `AI agent runtime distributed workers event log` |
| Context and memory | `long term memory agent benchmark LongMemEval LoCoMo`, `workspace context retrieval agent memory architecture` |
| Tool use | `tool calling benchmark agent BFCL MCP`, `function calling agent reliability benchmark` |
| Long context | `HELMET benchmark long context agents`, `RULER benchmark retrieval agents` |
| Web and OS agents | `WebArena OSWorld WorkArena AppWorld agent benchmark`, `computer use agent benchmark results` |
| Implementation | `FastAPI SSE agent streaming Redis streams`, `event sourcing AI agent platform` |
| Security and ops | `agent sandbox tool execution security`, `LLM agent observability tracing cost latency` |

## Source Priority

Prefer sources in this order:

1. Peer-reviewed papers, preprints, and workshop papers.
2. Benchmark papers with official project pages, datasets, leaderboards, or code.
3. Official benchmark pages, datasets, release notes, and source repositories
   that support a paper.
4. Official protocol or framework specifications when no paper exists, such as
   MCP specs or OpenTelemetry docs.
5. Engineering blog posts only when they include implementation detail,
   reproducible results, or links to primary sources.
6. Newsletters, social posts, and commentary only as discovery hints, not as
   promoted evidence.

The daily report should clearly mark paper-backed items. If fewer than three
paper-backed candidates are relevant in the search window, include the best
paper-backed candidates first and then fill remaining slots from lower-priority
source classes.

## Relevance Rubric

Score every candidate from 0 to 3 in each dimension:

| Dimension | Meaning |
| --- | --- |
| Feature fit | Could this become a Structure feature or design improvement? |
| Benchmark fit | Does it include a benchmark, dataset, metric, baseline, or result table? |
| Implementation detail | Does it explain enough to reproduce or adapt the approach? |
| Credibility | Is the source primary, reproducible, or backed by code/data? |
| Urgency | Is it new, trending, breaking compatibility, or strategically important? |

Keep candidates with a total score of 8 or higher. Include lower-scoring items
only when they are directly tied to an active roadmap item.

## Daily Record Format

Create one file per run:

```text
docs/research-radar/YYYY-MM-DD.md
```

Use this structure. Each daily record must contain both an English report and a
Simplified Chinese report:

```markdown
# Research Radar - YYYY-MM-DD

## English Report

### Scan Profile

- Project focus:
- Search window:
- Sources checked:

### High-Signal Findings

| Finding | Source | Relevance | Evidence | Difference from Structure | Benchmark comparison | Action |
| --- | --- | ---: | --- | --- | --- | --- |
|  |  |  |  |  |  |  |

### Benchmark Signals

| Benchmark or dataset | Reported metric/result | Compared systems | Applicability | Follow-up |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

### Feature Ideas

| Idea | Source | Why it matters | Suggested owner area | Effort |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

### Structure Gap Analysis

| Item | Similarity to Structure | Key differences | Reusable design | Risk or mismatch |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

### Current Benchmark Comparison

| Item | Closest local benchmark | Covered today? | Missing evaluator capability | Recommended benchmark work |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

### Watchlist

- 

### Follow-Up Tasks

- [ ] 

## 中文报告

### 扫描概况

- 项目关注点：
- 搜索窗口：
- 已检查来源：

### 高信号发现

| 发现 | 来源 | 相关性 | 证据 | 与 Structure 的差异 | 与当前 Benchmark 的对比 | 建议动作 |
| --- | --- | ---: | --- | --- | --- | --- |
|  |  |  |  |  |  |  |

### Benchmark 信号

| Benchmark 或数据集 | 报告指标/结果 | 对比系统 | 适用性 | 后续动作 |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

### Feature 想法

| 想法 | 来源 | 价值 | 建议归属区域 | 工作量 |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

### Structure 差异分析

| Item | 与 Structure 的相似点 | 关键差异 | 可复用设计 | 风险或不匹配 |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

### 当前 Benchmark 对比

| Item | 最接近的本地 Benchmark | 当前是否覆盖 | 缺失的评测能力 | 建议 Benchmark 工作 |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

### 观察列表

-

### 后续任务

- [ ]
```

## Bilingual Output Rules

- The Markdown record and the HTML email must contain both English and
  Simplified Chinese versions.
- The English version should appear first, followed by the Chinese version.
- Preserve paper titles, benchmark names, model names, code paths, class names,
  command names, URLs, and metric names in their original form unless there is a
  well-known Chinese translation.
- Every paper, benchmark, dataset, project page, and source repository mentioned
  as a finding must use a clickable Markdown link. Do not leave article titles
  or source URLs as plain text when a URL is known.
- The Chinese version should not be a vague summary. It should preserve the same
  decisions, relevance scores, item-level Structure differences, benchmark
  comparisons, and follow-up tasks as the English version.
- Avoid duplicate source crawling for translation. Translate the already
  selected findings and keep the same links and evidence.

## Item-Level Analysis Requirements

Every promoted item must include two explicit comparisons:

1. **Difference from Structure**
   - Compare the external system or paper against Structure's event-sourced
     run lifecycle, executor model, context store, plugin/tool registry,
     distributed worker model, streaming surface, security model, and
     observability roadmap.
   - Call out whether the item is a feature gap, a different design choice, an
     implementation detail Structure already covers, or a mismatch that should
     not be adopted.
2. **Current benchmark comparison**
   - Compare against the existing local benchmark harness and adapters:
     LongMemEval, LoCoMo, LightMem/MemBase baselines, HELMET, tau-bench, RULER,
     and the later benchmark backlog.
   - State whether the current benchmark suite already measures the capability,
     partially measures it, or misses it entirely.
   - If missing, name the smallest useful benchmark action: fixture, loader,
     scorer, evidence schema, baseline row, adapter, or full benchmark track.

Avoid generic impact statements. A useful item should say what Structure has
today, what the external source does differently, and how the benchmark harness
would need to change to verify the idea.

## HTML Email Report

After writing the Markdown record, send a rich-text bilingual HTML email to all
configured recipients. The email should be self-contained and easy to scan.

Configured recipients:

- `wanghw00@gmail.com`
- `2021101040028@whu.edu.cn`
- `1161252028@qq.com`

Use this subject:

```text
Structure Research Radar - YYYY-MM-DD
```

The HTML body should contain:

- an English section and a Simplified Chinese section
- a short executive summary with the number of promoted findings in both
  languages
- a top-priority list with relevance scores and clickable source links in both
  languages
- one section per item in both languages containing:
  - what it is
  - difference from Structure
  - comparison with current benchmarks
  - recommended action
- a benchmark delta table in both languages
- a follow-up task list in both languages
- a link or path to the Markdown record in the repository

Use plain, email-safe HTML: headings, paragraphs, tables, inline CSS, and source
links. Every article title or source label in the HTML email must be an
`<a href=\"...\">...</a>` link when a URL is available. Do not rely on external
images, scripts, remote stylesheets, or CSS that requires a web app runtime.

## Promotion Rules

Promote a finding into project work only when it meets one of these conditions:

- It maps to an existing benchmark adapter, scorer, or result table.
- It reveals a feature gap in executor runtime, context management, tool use,
  streaming, worker coordination, or observability.
- It includes reproducible benchmark data that can update `paper/` or
  `benchmarks/baselines/`.
- It has implementation details that can become a small spike, test fixture, or
  integration adapter.

Do not create implementation tasks for generic trend pieces, vendor marketing,
or papers without enough detail to reproduce their claims.

## Weekly Rollup

Once a week, consolidate daily records into:

```text
docs/research-radar/weekly-YYYY-Www.md
```

The weekly rollup should contain:

- top 5 findings
- benchmark deltas worth tracking
- feature ideas grouped by subsystem
- recommended next experiments
- stale watchlist items to drop
