# Tier-B Result Artifacts

This directory contains credential-free benchmark reports produced by the
Rust-native Tier-B harness. An artifact is evidence only at the level declared
by its `evidence_level` field.

## 2026-07-26 LongCat live smoke

- artifact: `2026-07-26-longcat-2.0-live-smoke.json`
- evidence level: `live_api`
- provider wire protocol: OpenAI Chat Completions
- endpoint: `https://api.longcat.chat/openai/v1`
- model: `LongCat-2.0`
- source revision: `2b08c163eebb55d522a80f56816f3f92e16530bd`
- source worktree: clean
- result: 1/1 task passed with an exact file-content match
- scope: one engineering smoke task, not a policy comparison or paper result

The API key was injected only into the benchmark process. It is not present in
the artifact or repository.
