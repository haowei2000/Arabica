# PiAgent, Codex CLI, and Structure comparison protocol

## Purpose

This protocol measures whether PiAgent changes task completion, fresh-token use,
and elapsed time relative to controlled Codex CLI or Structure under identical local tasks.
It does not treat either project's published benchmark as independent evidence.

PiAgent v1.5.5 was inspected at commit
`50abee4c57476862951d9e9422647a83449c7d65`. Its benchmark implementation already
supports the `piagent,codex-cli` surface pair, hidden graders, isolated
workspaces, deterministic ordering, exact JSONL usage, and explicit cost
confirmation. Structure follows those compatible semantics while retaining its
own fixture and verifier ownership.

## Controlled variables

- Same immutable suite digest, task prompt, fixture, verifier, model identity,
  reasoning level, timeout, and repetition count.
- Fresh workspace for every trial; no session resume or conversation reuse.
- Codex runs ephemerally with user configuration and repository rules disabled.
- PiAgent runs through its official `init-project.sh`, guard extension, and skill
  package. The runner then creates and commits the clean Git baseline expected
  by PiAgent's task contract. Deterministic bootstrap and Git metadata are not
  counted as task edits.
- Structure runs its real Rust Session, Runtime, provider adapter, canonical
  Event Log, and confined local runner. It is not simulated through another CLI.
- Pair order is deterministically randomized per task and repetition.
- Only synthetic fixtures are allowed. Prompts and output markers must never
  contain credentials or user data.

## Measurement

Codex reports provider input including cached input, so normalized fresh tokens
are `input_tokens - cached_input_tokens + output_tokens`. Pi reports uncached
input separately from `cacheRead`, so its fresh tokens are `input + output`.
Reasoning tokens, cache writes, tool calls, event counts, duration, verifier
score, and scope violations remain visible as separate fields.

The primary token comparison uses the geometric mean of PiAgent/Codex fresh-token
ratios only for pairs where both trials resolve. Completion counts remain an
independent quality gate: the report cannot label a token improvement unless
PiAgent is non-inferior on resolved tasks.

Raw stdout and stderr are captured incrementally, including before timeout,
then reduced to typed usage, progress diagnostics, and SHA-256 digests. The
diagnostics count model turns, tool-use turns without observed call items, and
failed tool results without persisting provider reasoning or transcript text.

Structure additionally stores exact provider wire exchanges in the private
trial directory. They are diagnostic evidence and must not be committed.

## Claim boundary

The bundled four-task suite has claim tier `controlled-local-pilot`. It can catch
harness regressions and provide an initial directional comparison, but it cannot
support a general quality, cost, or token-efficiency claim. A broader claim needs
generated hidden variants, multiple domains, enough paired repetitions for a
confidence interval, frozen runtime identities, and disclosed failures and
infrastructure retries.

Provider execution is deliberately gated by the CLI's literal `--yes`. Planning,
suite validation, verifier tests, and preflight are provider-free.
