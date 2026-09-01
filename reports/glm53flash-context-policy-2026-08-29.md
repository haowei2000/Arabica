# GLM-5.3-Flash context-policy pilot — 2026-08-29

## Status

This is real paid-provider evidence collected through the localhost
Responses-to-Chat compatibility adapter and the GLM Coding endpoint. It is a
context-policy pilot, not an agent ranking. The GLM credential came from the
existing 1Password item and was never written to the repository or artifacts.

Two different context questions were exercised and are reported separately:

- Four short repository fixtures compared Structure full replay with
  Structure's default short-memory projection.
- One Terminal-Bench `db-wal-recovery` pair compared B0 full replay with
  file-backed GC (FBGC).

The frozen local artifacts are under ignored paths in
`.local/benchmarks/context-policy-glm53flash-20260829-r1` and
`.local/benchmarks/harbor/`.

## Short-fixture pilot

All eight trials completed, passed the hidden verifier, stayed within the
allowed file scope, and preserved typed reasoning usage.

- `falsey-config`: full replay used 2,567 fresh tokens in 3 model calls;
  short memory used 3,855 in 4 calls.
- `bounded-retry`: full replay used 1,974 fresh tokens in 3 calls; short memory
  used 5,028 in 4 calls.
- `stable-dedupe`: full replay used 3,176 fresh tokens in 5 calls; short memory
  used 2,305 in 3 calls.
- `config-batch`: full replay used 4,035 fresh tokens in 4 calls; short memory
  used 5,461 in 3 calls.

The success-conditioned geometric mean of full-replay fresh tokens divided by
short-memory fresh tokens was 0.7183. Full replay won three of four token pairs;
short memory won one. This is not evidence that full replay is generally
better: the two arms sampled different model trajectories, and a single extra
model turn dominated several pairs.

The treatment was active rather than nominal. Six short-memory requests
contained compact `<short_memory_batch>` projections. File-backed archive GC
was not enabled in this harness, so these trials must not be described as an
FBGC comparison.

## Terminal-Bench B0 versus FBGC pilot

The first B0 launch used the invalid API type spelling `openai-responses` and
failed before any model call. It is retained as an infrastructure failure and
excluded. The corrected Rust enum spelling was `open_ai_responses`.

The valid `db-wal-recovery` pair produced:

- B0: Harbor reward 1.0, terminal success, 7 provider calls, 8 tool calls,
  23,270 input tokens, 17,280 cached input tokens, 3,647 output tokens, 1,911
  reasoning output tokens, and 9,637 fresh tokens.
- FBGC: Harbor reward 1.0, terminal success, 9 provider calls, 10 tool calls,
  52,060 input tokens, 34,048 cached input tokens, 6,995 output tokens, 4,751
  reasoning output tokens, and 25,007 fresh tokens.

FBGC admitted one compaction at model step 8 and produced 16 pointer
appearances. The admission was too late to satisfy the predeclared trajectory
gate: only two provider calls followed it, and no later archive hydration was
needed. The GC quality gate therefore failed. The pair demonstrates that the
FBGC path executed and preserved task quality, but it does not demonstrate a
token advantage; the FBGC trajectory used more model/tool turns and substantially
more fresh tokens.

## Interpretation and next gates

- Do not combine the short-memory and FBGC measurements; they test different
  policies.
- Do not interpret either result as an agent-stack comparison.
- Repeat paired trials are required to separate model sampling variance from
  context-policy effects.
- FBGC qualification should use tasks where an admission occurs early enough
  to create at least four post-admission provider calls and a later exact
  archive read, as required by the existing gate.
- The completed Harbor trials used report schema v8, whose top-level aggregate
  omitted reasoning tokens even though every provider-call observation retained
  them. The harness is now fixed in v9; the v8 totals above were reconstructed
  losslessly from those typed call observations.
- A formal Codex agent-stack run remains blocked until the adapter supports the
  unmodified Codex hosted and namespace tool surface.

## Substitutive-pointer repair

Post-run wire inspection found that FBGC pointers replaced `run_memory` entries
but the Runtime independently reconstructed the complete active-run
`continuation`. Archived reasoning, function calls, and function outputs were
therefore still sent beside the pointers. The admission calculation measured a
projection-layer saving that did not exist in the final Provider request.

The Runtime now derives a typed continuation-substitution set from file-backed
pointer provenance. A directly archived model-response event is removed from
continuation, and an archived tool batch removes both the matching function
call and function output by `call_id`. Non-archived current tool cycles remain
exact. Event Log history and file archives are unchanged and remain lossless.

Regression coverage checks all of the following:

- archived reasoning is absent from continuation;
- an archived function call and output are removed atomically;
- an unarchived call and output remain paired;
- serialized `pointers + remaining continuation` is smaller than the original
  full continuation for a large archived result.

Two real GLM validation runs followed the repair:

- With the formal 8-batch checkpoint, `db-wal-recovery` completed in six calls
  with Harbor reward 1.0. It finished before an admission, validating the
  unchanged non-compaction path but not substitution itself.
- A separately labelled diagnostic run used a 2-batch checkpoint to force an
  early admission. The archived call and its output were present in the exact
  file archive and absent from the next Provider wire request; its archived
  reasoning item was also replaced by a pointer. The request remained
  protocol-valid and the task received Harbor reward 1.0. Admission occurred at
  step 4 with five post-admission calls. No later archive read was required, so
  the full GC quality gate remained false as designed.

The diagnostic run proves replacement semantics and protocol validity. It is
not included in the formal token comparison because the checkpoint setting was
intentionally changed to exercise the repaired branch.
