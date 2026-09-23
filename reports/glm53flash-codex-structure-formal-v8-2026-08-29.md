# GLM-5.3-Flash Codex CLI vs Structure Formal Campaign v8

Date: 2026-08-29

## Decision

Structure is the formal winner in this frozen 20-pair native agent-stack
campaign.

- Strict resolution: Codex CLI 20/20; Structure 20/20.
- Comparable double-success pairs: 20.
- Economic-token wins: Structure 20; Codex CLI 0; ties 0.
- Geometric mean `Codex / Structure` economic-token ratio: **2.8634x**.
- Paired 10,000-resample bootstrap 95% CI: **[2.3042x, 3.5942x]**.
- Mean `Codex / Structure` duration ratio: **1.7310x**.
- Harness decision: `overall_winner = structure`.

Economic tokens are fresh input plus output. Cached input is subtracted from
input. Reasoning tokens are a subset of output and are not counted twice.

## Frozen treatment

- Four unchanged scenarios from `benchmarks/cli-comparison/suite.json`.
- Five repeats per scenario and surface, 40 trials total.
- Deterministically interleaved execution order from seed `20260829`.
- Model: `glm/glm-5.3-flash`; requested thinking: `high`.
- Timeout: 900 seconds per trial.
- Official unmodified Codex CLI `0.150.1`.
- Codex executable SHA-256:
  `a14f9a907c12c8812878b70e6b7d65f81c39ed795513e46a55817d7428c0ca6b`.
- Release harness SHA-256:
  `961d7d8add33ee1cc696a256eec37cfda54df8748a956e81ea7eddd8d3340457`.
- Structure dirty-tree digest at preflight:
  `3aa8539c9fd91564f6c8eba23236631622ee81c4e328f2451c7ff3fa49185af0`.
- Responses-to-Chat adapter SHA-256:
  `f8a36f52a2e693220fb2fb36010f9804a7e2343ca068a3a4dfc6248290ba040b`.

Codex ran with an isolated home, ignored personal configuration and rules, and
kept its default system prompt, normal tools, `multi_agent_v1` namespace, and
`web_search` declaration. Namespace children were encoded as unique GLM Chat
function names and restored losslessly to Responses `namespace + name` output.
No trial selected hosted web search. Had it been selected, the trial would have
been retained as an infrastructure failure because GLM Chat Completions cannot
perform Responses provider-hosted search; the adapter does not fabricate search
results or silently disable the tool.

The frozen suite digest covers fixtures, prompts, verifiers, the adapter, and
its protocol tests. No code or experiment input changed after the manifest was
created.

## Aggregate measurements

| Metric | Codex CLI | Structure |
|---|---:|---:|
| Resolved | 20/20 | 20/20 |
| Fresh input | 177,667 | 54,342 |
| Output | 29,382 | 22,116 |
| Economic fresh tokens | 207,049 | 76,458 |
| Total input | 1,744,323 | 93,126 |
| Cached input | 1,566,656 | 38,784 |
| Reasoning output | 6,270 | 12,906 |
| Tool calls | 99 | 75 |
| Total duration | 954.787 s | 602.380 s |
| Timeouts | 0 | 0 |
| Scope failures | 0 | 0 |

Structure used 63.1% fewer aggregate economic fresh tokens. The primary paired
estimate is the geometric mean and bootstrap interval above, not this pooled
aggregate percentage.

## Per-scenario raw economic-token observations

- `falsey-config`: Codex [8,222, 6,321, 9,525, 8,364, 19,099]; Structure
  [4,146, 1,826, 3,255, 1,718, 4,202].
- `bounded-retry`: Codex [13,577, 7,740, 9,283, 7,552, 8,112]; Structure
  [6,508, 1,635, 4,728, 5,540, 2,788].
- `stable-dedupe`: Codex [11,616, 8,586, 6,467, 7,519, 10,139]; Structure
  [1,245, 3,008, 1,048, 4,148, 3,987].
- `config-batch`: Codex [8,715, 18,341, 7,902, 21,872, 8,097]; Structure
  [4,469, 5,585, 5,961, 5,559, 5,102].

All observations, including high-cost long-tail Codex runs, are retained.

## Audit and limitations

Every trial report records raw stdout and stderr paths plus SHA-256 digests. An
independent post-run audit recomputed all 80 hashes with zero mismatches.

The GLM model is unknown to Codex's built-in model catalog, so Codex reports
that it uses fallback model metadata. This is a real limitation of requiring
the same third-party model across agents, and the warning is retained in raw
stderr. The adapter buffers the upstream Chat response before emitting
Responses SSE, so wall-clock results include this compatibility layer and are
secondary evidence. The primary token and strict-quality comparison remains
valid for this pinned model, adapter, suite, and official Codex version; it is
not a universal claim over other models or repository classes.

## Reproduction artifacts

- Manifest: `.local/benchmarks/formal-glm53flash-20260829-v8/codex-structure/manifest.json`
- Preflight: `.local/benchmarks/formal-glm53flash-20260829-v8/codex-structure/preflight.json`
- Adapter hashes: `.local/benchmarks/formal-glm53flash-20260829-v8/codex-structure/adapter.sha256`
- Trial reports: `.local/benchmarks/formal-glm53flash-20260829-v8/codex-structure/trials/`
- Raw process outputs: `.local/benchmarks/formal-glm53flash-20260829-v8/codex-structure/workspaces/*/process-output/`
- Summary: `.local/benchmarks/formal-glm53flash-20260829-v8/codex-structure/summary.json`
