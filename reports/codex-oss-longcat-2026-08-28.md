# Official Codex CLI comparison: provider quota blocked

Date: 2026-08-28

## Outcome

Downloaded the unmodified official Codex CLI release and its matching source.
The CLI successfully completed a synthetic local Responses SSE smoke test.
The live LongCat comparison could not produce quality measurements: the team
key returned HTTP 402 with an explicit token-quota exhaustion message on the
non-streaming route, while the minimal streaming request returned HTTP 500.
Remaining paid trials were stopped. No coding-quality, token-efficiency, or
latency advantage is established for either agent.

## Pinned upstream

- Repository: <https://github.com/openai/codex>
- Official release: <https://github.com/openai/codex/releases/tag/rust-v0.150.1>
- CLI version: `codex-cli 0.150.1`
- Source commit: `90854393966b21e9ebfd21b122334eb09a20c93d`
- Asset: `codex-aarch64-apple-darwin.tar.gz`
- Verified archive SHA-256:
  `f66f1c45f1eda49d6a8aef86faee24121b0c8913cd9023f23ee44262606fc7b6`
- Local source and executable: `.local/benchmarks/codex-v0.150.1/` (git-ignored).
- The existing PATH resolved to ChatGPT.app's `0.150.0-alpha.8`; this run used
  the explicit standalone release path, not that bundled executable.
- The binary was downloaded from the official release, not compiled locally.

## Live diagnostic pair

Manifest: `/tmp/structure-codex-oss-longcat-20260828-r1/manifest.json`.
Configuration: `longcat/LongCat-2.0`, reasoning `high`, 300-second timeout,
seed `20260828`, four tasks, one repeat per surface, eight planned trials.

Only the first `falsey-config` pair was attempted:

- Codex CLI exited 1 after 56.544 seconds, with no file edits or observed
  usage. Its stream retry messages reported high demand.
- Structure exited 1 after 1.517 seconds, with no file edits or observed
  usage. The saved provider response has HTTP status 402 and explicitly says
  the token quota is insufficient (`too_many_requests`, `rate_limit_error`).

The durations measure different error/retry behavior, not coding performance.
Missing usage is not proof of zero billing. The remaining six trials were not
attempted. These artifacts are diagnostic only, not a completed benchmark.

Two additional minimal synthetic requests used the same key, model, reasoning
effort, and input, with `max_output_tokens=256`:

- `stream=false`: HTTP 402, JSON quota-exhaustion error, 1.114 seconds.
- `stream=true`: HTTP 500, JSON rather than SSE, 0.478 seconds; no
  `response.completed` event. Its relationship to quota exhaustion is not
  established by the response, so it is recorded as a separate observation.

No credential or authorization header was printed or saved. The key was read
through 1Password CLI after successful desktop authorization (no 1Password
MCP tool was available), kept in a process environment, then unset and the
shell exited. Raw synthetic provider artifacts remain under the temporary
diagnostic directory and are not committed.

The diagnostic artifacts and prepared r2 manifest are also archived under
`benchmark_runs/codex-oss-longcat-20260828/{diagnostic-r1,prepared-r2}/`
(git-ignored), so they survive temporary-directory cleanup. No credentials
are present in that archive.

## Source-backed interpretation and offline check

The pinned source requests `Accept: text/event-stream` in
`codex-rs/codex-api/src/endpoint/responses.rs`. Its SSE parser in
`codex-rs/codex-api/src/sse/responses.rs` requires a `response.completed` event.
`codex-rs/protocol/src/error.rs` maps `InternalServerError` to the high-demand
message. That message alone does not diagnose model saturation.

The LongCat model-list response uses `object/list/data`, while the Codex
metadata loader expected `models`. Codex logged the mismatch and selected
fallback metadata. The same fallback warning occurred in the successful
offline smoke test, so it is not by itself a fatal startup error.

The local smoke script used a fresh Codex home, a localhost server, a synthetic
key, and the unmodified release. It observed one streamed POST to
`/v1/responses`, an assistant `SYNTHETIC_OK` message, `turn.completed`, and exit
code 0. Token counts in that test are fabricated fixture values and must never
be included in performance results. This verifies startup and response parsing,
not real-model reasoning, tool execution, or provider compatibility.

## Harness isolation and resumption

The original `--ignore-user-config` flag does not isolate personal auth,
history, or state. The harness now creates a fresh `codex-home` outside the
scored project, passes it only to the Codex child, and disables ancestor
AGENTS.md loading with `project_doc_max_bytes=0`. Personal configuration and
execpolicy rules remain disabled. This changes configuration/persistence
isolation, not the upstream CLI or Structure's production runtime.

A new unexecuted manifest is prepared at
`benchmark_runs/codex-oss-longcat-20260828/prepared-r2/manifest.json` (also in
`/tmp/structure-codex-oss-longcat-20260828-r2/manifest.json`). After quota is
restored, rebuild the runner, inject the same approved team key, run preflight,
and start with one pair using that new root. Do not mix r1 diagnostics with r2
results. Continue the other tasks only if the first pair gets past provider
errors. Model metadata and tool/schema compatibility still need real validation.

## Verification

- Release archive checksum: passed.
- Official CLI `--version` and `exec --help`: passed.
- Local synthetic SSE smoke: passed.
- JavaScript syntax checks for local diagnostics: passed.
- Rust formatting and `git diff --check`: passed.
- Benchmark library tests: 34 passed, including six CLI-comparison tests.
- Workspace Clippy (`--all-targets -- -D warnings`): passed.

No commit or push was requested or performed. Pre-existing workspace edits
were preserved.
