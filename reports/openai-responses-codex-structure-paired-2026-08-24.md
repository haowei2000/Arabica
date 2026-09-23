# OpenAI Responses adapter and Codex CLI paired pilot

Date: 2026-08-24

## Outcome

Structure now has a real `open_ai_responses` provider adapter. The adapter
encodes OpenAI Responses requests, decodes messages, reasoning, and function
calls into typed Runtime items, retains the exact UTF-8 provider response body
in the immutable Event Log, and replays original output items for stateless
continuation. Reasoning-token usage is preserved separately from total output
tokens.

The first live LongCat trial exposed a boundary bug that unit fixtures had not
shown: the first function-call continuation was exact, but later model steps
allowed ShortMemory GC to replace earlier reasoning/tool batches with lossy
keys. The Event Log was still lossless, but the next Responses request no
longer contained every prior output item and function result. On
`bounded-retry`, this caused six read/search calls, two failed results, and no
edit.

The fix separates the two projections:

- ShortMemory remains a deliberately lossy context projection.
- Provider continuation is reconstructed directly from ordered active-run
  `model.response.item` and `tool.call.completed` events.
- Provider-originated message, reasoning, and function-call state is retained
  and foreign continuation state is rejected rather than discarded.
- The provider compiler removes duplicate typed run-memory items when the
  exact Event Log continuation is present.

After the fix, the same `bounded-retry` trial passed in three model turns and
two tool calls with no failed tools.

## LongCat Responses paired run

The frozen r2 manifest used LongCat-2.0, reasoning effort `high`, the same
LongCat OpenAI Responses base URL and team key environment, four JavaScript
tasks, one repetition, fresh workspaces, deterministic pair ordering, and a
900-second outer timeout.

| Task | Surface | Resolved | Duration | Fresh tokens | Cached input | Output | Reasoning | Model turns | Tools | Failed tools |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| falsey-config | Codex CLI | no | 58.105 s | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| falsey-config | Structure | yes | 15.437 s | 1,042 | 1,792 | 367 | 142 | 3 | 2 | 0 |
| bounded-retry | Codex CLI | no | 58.751 s | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| bounded-retry | Structure | yes | 24.298 s | 1,710 | 1,664 | 740 | 393 | 3 | 2 | 0 |
| stable-dedupe | Codex CLI | no | 58.783 s | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| stable-dedupe | Structure | yes | 24.354 s | 2,039 | 1,536 | 850 | 566 | 3 | 2 | 0 |
| config-batch | Codex CLI | no | 58.067 s | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| config-batch | Structure | yes | 29.426 s | 2,619 | 1,408 | 991 | 655 | 3 | 4 | 0 |

Aggregate Structure result: 4/4 resolved, mean duration 23.379 seconds,
mean fresh tokens 1,852.5, mean reasoning tokens 439, mean 3 model turns,
and zero failed tools. Codex CLI resolved 0/4; every run exited after roughly
58 seconds before an observed model turn, token, tool call, or file change.

There are zero comparable success pairs, so no agent-quality token or duration
ratio is reported. This run establishes a narrower availability result: with
the tested LongCat Responses compatibility route, Structure's non-streaming
adapter completed all four tasks while Codex CLI's Responses path failed before
model execution. It does not establish that Structure is a better coding agent
than Codex CLI.

## Evidence boundary

- Formal r2 artifacts: `/tmp/structure-codex-vs-structure-responses-20260824-r2`
- Superseded diagnostic r1 artifacts:
  `/tmp/structure-codex-vs-structure-responses-20260824-r1`
- r1 is excluded from the formal summary because it contains the continuation
  bug discovered during live validation.
- Raw Responses bodies can contain prompts, reasoning, and tool results and
  remain private temporary artifacts. Credentials were injected from
  1Password and were not written to reports or raw exchanges.

## Verification

- focused provider codec and HTTP adapter tests
- exact active-run continuation projection test
- Runtime regression suite
- full workspace build, tests, formatting, and Clippy gates before handoff
