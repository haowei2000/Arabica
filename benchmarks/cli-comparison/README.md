# Agent-stack and context-policy benchmarks

This harness deliberately separates two different claims. Their results must
never be pooled into one ranking:

- `agent-stack` compares complete agents. Each agent keeps its own native system
  prompt, tool/MCP surface, loop, and context implementation. The user task,
  fixture, requested model/thinking, timeout, repeat schedule, allowed changes,
  and verifier are frozen.
- `context-policy` is a one-variable A/B inside Structure. Both arms use the
  same Runtime, provider and wire protocol, system/user input, tool schemas and
  runner, fixture, limits, and verifier. The only treatment is
  `full_replay` versus Structure's file-backed GC policy.

The manifest encodes the benchmark mode and rejects mixed surfaces. Agent-stack
mode accepts two native surfaces from PiAgent, Codex CLI, and Structure.
Context-policy mode accepts exactly `structure-full-replay` and
`structure-file-backed-gc`; both dispatch through the same Structure execution
function, with only the memory/compaction policy differing.

The suite independently checks four small repository tasks with hidden verifier
scripts. Workspaces are fresh and trial order is deterministically randomized
within each pair.

The runner records typed results, file-scope changes, normalized usage, duration,
and SHA-256 digests of stdout/stderr. It intentionally does not retain raw model
transcripts. Write manifests and reports outside this directory because the
manifest freezes the complete suite tree digest.

Planning and preflight never call a model:

```bash
cargo run -p structure-short-memory-benchmark --bin cli_comparison -- plan \
  --mode agent-stack \
  --suite benchmarks/cli-comparison/suite.json \
  --output /tmp/arabica-cli-comparison/manifest.json \
  --model openai/gpt-5.6-sol \
  --thinking xhigh \
  --piagent-package-root /path/to/piagent \
  --surfaces piagent,codex-cli

cargo run -p structure-short-memory-benchmark --bin cli_comparison -- preflight \
  --manifest /tmp/arabica-cli-comparison/manifest.json
```

Plan a context-policy A/B without specifying surfaces (the exact two treatments
are selected by the mode):

```bash
cargo run -p structure-short-memory-benchmark --bin cli_comparison -- plan \
  --mode context-policy \
  --suite benchmarks/cli-comparison/suite.json \
  --output /tmp/structure-context-policy/manifest.json \
  --model openai/gpt-5.6-sol \
  --thinking xhigh \
  --provider-base-url http://127.0.0.1:4119/v1 \
  --provider-api-key-env BENCHMARK_PROXY_TOKEN
```

Running a trial can consume provider quota and therefore requires `--yes`:

```bash
cargo run -p structure-short-memory-benchmark --bin cli_comparison -- run \
  --manifest /tmp/arabica-cli-comparison/manifest.json \
  --trial-id falsey-config-r01-piagent \
  --output-root /tmp/arabica-cli-comparison \
  --yes
```

Only paired trials where both surfaces pass are included in the fresh-token
ratio. A formal token winner requires at least five eligible pairs, quality
non-inferiority by resolved-trial count, and a deterministic 10,000-resample
paired bootstrap whose 95% confidence interval for the geometric-mean fresh
token ratio lies wholly on the winning side of 1.0. A point estimate alone is
never reported as an improvement. This four-task suite is a local pilot, not a
general product claim.

The cost metric is intentionally claim-specific: `context-policy` compares
uncached/fresh **input** only, so shorter model answers cannot masquerade as
context savings; `agent-stack` compares fresh input plus output as the complete
agent's economic cost. Input, cache-read/cache-write, output, reasoning output,
tool-call count, and duration remain separately available in every report.

For the real Structure Runtime, select `--surfaces piagent,structure` or
`--surfaces codex-cli,structure` and provide
`--provider-base-url` plus `--provider-api-key-env`. The key itself must remain
in the process environment. Structure and Codex CLI both use the configured
OpenAI Responses dialect, including the requested reasoning effort. PiAgent
trials initialize and commit a clean Git
baseline after `init-project.sh`, matching the lifecycle expected by its task
contract.

For Codex comparisons, pass `--codex-program` with an explicit official release
binary, and record its version, source tag/commit, and release checksum. Do not
assume the `codex` on PATH is the standalone open-source CLI: desktop apps may
bundle a different version. Codex trials now use a fresh per-trial `codex-home`
outside the scored project tree, ignore personal configuration and execpolicy
rules, and disable ancestor AGENTS.md loading. Supply provider credentials via
the configured environment variable; personal Codex login state is not reused.

Run one pair first. Stop additional paid trials if the provider fails before
model execution (for example, exhausted quota). Missing usage must not be
reported as proof of zero billing, and infrastructure failures must not be
used to claim coding-quality or speed advantages. Use a new manifest/output
root after changing the harness; keep diagnostic runs separate from formal
results.

## Responses-to-Chat compatibility proxy

`responses-chat-proxy.mjs` is a localhost-only, stateless adapter for controlled
comparisons against providers that expose Chat Completions but not Responses.
It converts text messages, developer instructions, reasoning content, function
definitions/calls/results, tool choice, usage, and completed JSON/SSE responses.
It rejects custom, namespace, hosted, and multimodal tools/items rather than
silently dropping them. Because current Codex includes namespace and hosted
tools by default, this proxy is diagnostic infrastructure only; do not disable
Codex features to manufacture a comparable run.

The upstream key must be supplied as `GLM_API_KEY`; clients use an unprivileged
local placeholder token. The proxy never logs authorization headers, prompts,
tool arguments, reasoning text, or response content. Its stdout contains only
request shape, model, usage, output types, and duration metadata.

```bash
GLM_API_KEY="$(op read 'op://vault/item/password')" \
GLM_CHAT_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4 \
GLM_PROXY_CLIENT_TOKEN=local-proxy \
GLM_PROXY_PORT=4119 \
node benchmarks/cli-comparison/responses-chat-proxy.mjs

node --test benchmarks/cli-comparison/responses-chat-proxy.test.mjs
```

This adapter changes the wire protocol and buffers upstream Chat Completions
before emitting Responses SSE. Record that treatment in every report; do not
compare transport latency with a native Responses endpoint or claim general
provider compatibility from it.

Changing Chat Completions/Responses translation or shared base-tool plumbing is
permitted when it is infrastructure work needed to represent the original
request and response without loss. For agent-stack comparisons, the adapter
must preserve every tool kind required by each unmodified agent; unsupported
native tools block that provider pair. Infrastructure changes must not rewrite
the task, system prompt, tool semantics, success criteria, or selectively grant
one agent a capability. For context-policy A/B, both arms must use the same
adapter and exact infrastructure revision.

## Fair-comparison policy

After a manifest is frozen, do not edit its suite, fixtures, prompts, allowed
changes, verifiers, model, thinking level, timeout, or agent treatment. Do not
disable or replace capabilities of a comparison agent to accommodate a provider
or improve a result. Agent-stack optimization is limited to Structure code plus
neutral compatibility/tool infrastructure. Context-policy implementation work
must be shared by both arms except for the selected policy. Every Structure or
infrastructure change requires a fresh manifest and a full rerun of both arms
against the unchanged suite.

Isolation that prevents personal state from entering a run is permitted, but
it must not alter the agent's default feature/tool surface. Place formal trial
workspaces outside the Structure repository so ancestor project instructions
cannot contaminate either treatment. A compatibility adapter must support the
comparison agent's unmodified protocol and tools; otherwise mark the provider
pair blocked rather than changing the agent.
