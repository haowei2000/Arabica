# GLM-5.3-Flash diagnostic: not a fair Codex vs Structure benchmark

Date: 2026-08-29

> Superseded for ranking by the valid 20-pair campaign in
> `reports/glm53flash-codex-structure-formal-v8-2026-08-29.md`. This file is
> retained as the failed/invalid diagnostic history.

## Status: invalid for agent ranking

The provider calls, file changes, verifier outcomes, token counts, and durations
below are real observations. However, this run disabled Codex web search and
multi-agent features to fit a Responses-to-Chat adapter that cannot represent
Codex's default namespace and hosted tools. That changes one comparison agent's
treatment and violates the project's fair-comparison policy.

Do not use this run to claim that Structure is better, faster, or more
token-efficient than Codex. Preserve it only as adapter and harness diagnostic
evidence. A valid rerun must leave Codex's default feature/tool surface intact,
keep the test suite frozen, and permit optimizations only in Structure code.

## Observed diagnostic outcome

Both agents resolved all four tasks with full verifier scores, only allowed file
changes, and zero failed tools. In this controlled pilot, Structure used fewer
fresh tokens on every task. The geometric mean Codex/Structure fresh-token ratio
was 3.03x. Mean wall-clock duration was 50.51 seconds for Codex and 40.49
seconds for Structure, so Structure was 19.8% lower on mean duration.

These figures describe the invalidated treatment only and support no comparative
agent conclusion.

## Frozen treatment

- Model: `glm-5.3-flash` (explicit; no alias substitution).
- Upstream: GLM Coding Plan Chat Completions at
  `https://open.bigmodel.cn/api/coding/paas/v4`.
- Thinking: enabled; requested effort `high` at the Responses boundary.
- Repeats: one per task and surface.
- Timeout: 300 seconds per trial.
- Seed: `20260829`, with deterministic within-pair ordering.
- Codex binary: unmodified official `codex-cli 0.150.1`, source tag
  `rust-v0.150.1`, source commit
  `90854393966b21e9ebfd21b122334eb09a20c93d`, arm64 release archive SHA-256
  `f66f1c45f1eda49d6a8aef86faee24121b0c8913cd9023f23ee44262606fc7b6`.
  Its runtime treatment was modified by disabling web search and multi-agent,
  so the overall Codex agent was not an untouched control.
- Structure: current Rust workspace and the real Session/Runtime/LocalRunner
  loop, not a fixture agent.
- Formal artifacts:
  `benchmark_runs/codex-oss-glm53flash-20260829-r2/` (git-ignored).
- Superseded diagnostic artifacts:
  `benchmark_runs/codex-oss-glm53flash-20260829-r1/`.

## Per-task results

- `falsey-config`: Codex 25.271 s, 11,700 fresh tokens, 101 reasoning
  tokens, 2 tools; Structure 28.560 s, 2,062 fresh tokens, 514 reasoning
  tokens, 2 tools. Both passed.
- `bounded-retry`: Codex 67.858 s, 18,815 fresh tokens, 395 reasoning
  tokens, 6 tools; Structure 50.274 s, 5,322 fresh tokens, 1,755 reasoning
  tokens, 2 tools. Both passed.
- `stable-dedupe`: Codex 50.746 s, 7,156 fresh tokens, 205 reasoning
  tokens, 5 tools; Structure 31.681 s, 2,321 fresh tokens, 713 reasoning
  tokens, 2 tools. Both passed.
- `config-batch`: Codex 58.183 s, 8,003 fresh tokens, 204 reasoning
  tokens, 6 tools; Structure 51.444 s, 5,868 fresh tokens, 1,142 reasoning
  tokens, 6 tools. Both passed.

## Aggregate observations

- Success: Codex 4/4; Structure 4/4.
- Fresh-token wins: Structure 4; Codex 0.
- Total fresh tokens: Codex 45,674; Structure 15,573.
- Mean fresh tokens: Codex 11,418.5; Structure 3,893.25.
- Geometric mean paired fresh-token ratio, Codex/Structure: 3.0305x.
- Total input tokens: Codex 254,670; Structure 15,950.
- Cached input: Codex 213,888; Structure 6,208.
- Total output tokens: Codex 4,892; Structure 5,831.
- Reasoning tokens: Codex 905; Structure 4,124.
- Tool calls: Codex 19; Structure 12.
- Total duration: Codex 202.058 s; Structure 161.959 s.
- Mean duration: Codex 50.5145 s; Structure 40.48975 s.
- Mean of paired Codex/Structure duration ratios: 1.2418x.
- Failed tools: zero for both.

The main efficiency difference is prompt/context overhead rather than shorter
answers: Codex had substantially more input and cache traffic, while Structure
actually emitted more output and explicit reasoning tokens. Cached tokens are
excluded from the benchmark's fresh-token calculation except for output, as
defined by the frozen runner.

## Compatibility adapter

GLM's Coding endpoint returned HTTP 404 for both streaming and non-streaming
`/responses`, while `/chat/completions` succeeded and included
`reasoning_content`. Current official Codex rejects `wire_api="chat"`, so a
localhost, stateless adapter was required.

`benchmarks/cli-comparison/responses-chat-proxy.mjs` converts:

- Responses text and developer messages to Chat messages, mapping developer to
  system because GLM rejected the developer role;
- Responses reasoning summaries to/from GLM `reasoning_content`;
- function definitions, tool choice, function calls, call IDs, and tool results;
- Chat usage details to Responses input/output/cached/reasoning usage;
- completed Chat responses to Responses JSON or completed SSE event sequences.

The adapter deliberately rejects unsupported custom, namespace, hosted, or
multimodal tools/items. Codex web search and multi-agent tools were disabled.
This is precisely why the run is invalid for comparative claims, even though
core Codex tools remained enabled. The adapter buffers upstream output, so this
run must not be compared to native streaming latency.

The proxy binds only to `127.0.0.1`. The real GLM team key was read from the
user-authorized 1Password item into the proxy process environment and was never
printed, written to artifacts, or passed to either agent. Both clients used a
non-secret localhost placeholder token. Proxy logs contain only model, request
shape, usage, output types, and duration; they omit prompts, tool arguments,
reasoning text, response content, and authorization headers.

Structure's configured raw exchange artifacts contain synthetic task prompts,
tool data, and model reasoning and remain in the git-ignored benchmark directory.
They contain no authorization header. Treat them as private audit evidence.

## Diagnostics excluded from the formal result

The first Structure attempt in r1 correctly changed `src/config.js`, used three
model turns and two tools, and passed the verifier when invoked with its actual
absolute workspace path. The harness had supplied a relative output root, so
the verifier resolved the workspace relative to the suite directory and
reported a false missing-file failure. The runner now resolves output roots to
absolute paths. All r1 measurements are excluded; r2 was planned and executed
after rebuilding the fixed runner.

## Evidence boundary

- The four tasks are small, JavaScript-only, and use hidden deterministic
  verifiers. One repeat is insufficient to estimate run-to-run variance.
- The agents have different native tool sets and system prompts. This compares
  complete agent stacks, not model-only inference or identical prompt bytes.
- The shared model and upstream are controlled, but the proxy is not a native
  Responses implementation and exposes reasoning as a replayable summary.
- Equal 4/4 quality here establishes pilot non-inferiority only. Broader claims
  require more tasks, repositories, languages, repeats, and native-provider
  comparisons.

## Verification

- Responses-to-Chat protocol tests: 4 passed.
- CLI-comparison focused Rust tests: 6 passed.
- Official Codex real GLM text turn: passed with `turn.completed`.
- Official Codex real GLM tool loops: passed in all four formal tasks.
- Formal report count: 8/8 artifacts; paired success count: 4/4.
- Formatting and `git diff --check`: passed.
- Workspace Clippy (`--all-targets -- -D warnings`): passed.

No commit or push was requested or performed. Existing unrelated workspace
changes were preserved.
