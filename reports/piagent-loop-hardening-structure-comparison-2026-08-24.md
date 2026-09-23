# PiAgent loop diagnosis, Structure hardening, and paired pilot

Date: 2026-08-24

## Outcome

The earlier PiAgent timeout was reproducible as an invalid benchmark startup,
not as evidence that PiAgent normally loops. The fixture had been initialized
with `init-project.sh` but had not been placed into the clean Git steady state
used by PiAgent's own benchmark runner. Its mutation contract failed to start,
and the LongCat/Pi telemetry then recorded long runs of `toolUse` turns without
an observed dispatchable tool call.

The corrected runner initializes PiAgent first, creates and commits a clean Git
baseline, freezes the task snapshot after bootstrap, and excludes `.git` from
task-scope accounting. With that correction, both PiAgent tasks completed and
no `toolUse`-without-call turn was observed.

Structure was hardened independently so the same failure class cannot consume
an unbounded trajectory:

- the exact provider-neutral response remains in the canonical Event Log;
- inconsistent terminal metadata adds a typed `model.response.rejected` event
  before `run.failed` and terminates immediately;
- legal tool calls that produce no successful state-changing action are bounded
  by a configurable no-progress limit and emit `agent.loop.terminated`;
- the confined local runner now exposes typed `read_file` as well as
  `write_file`, allowing the real Structure loop to run repository fixtures;
- external benchmark timeouts retain partial stdout, stderr, usage, and provider
  observations instead of reporting zero usage;
- Pi JSONL is reduced to explicit progress diagnostics, including consecutive
  `toolUse` turns without an observed tool-call item.

## Failure chain and correction

The original falsey-config PiAgent run lasted 900 seconds. Its local state
contained 81 assistant turns but only seven actual tool calls. After an initial
edit was rejected for lacking a session-bound task contract,
`piagent_task_start` failed, a broad Git action was policy-blocked, and turns
22 through 80 ended with `stopReason=toolUse` without a corresponding observed
tool call.

This evidence did not prove whether the item was lost in the LongCat Responses
wire, Pi's response decoder, dispatch, or PiAgent telemetry. The corrected
benchmark therefore avoids claiming a provider root cause. It fixes the known
fixture violation and makes the remaining observable invariant explicit.

After clean Git initialization, the same falsey-config task completed in 58.8
seconds with eight model turns and zero empty-tool-use turns. The loop therefore
disappeared under the upstream-compatible startup condition.

## Paid paired pilot

All four trials used LongCat-2.0, high thinking, the same immutable two-task
suite, one repetition, fresh workspaces, and a 600-second outer timeout. The
team key was injected from 1Password and was not written to any artifact.

| Task | Surface | Passed | Duration | Fresh tokens | Cached input | Output | Reasoning reported | Model turns | Tool calls | Failed tools |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| falsey-config | PiAgent | yes | 58.759 s | 8,522 | 18,944 | 2,026 | 705 | 8 | 6 | 2 |
| falsey-config | Structure | yes | 13.161 s | 2,631 | 0 | 367 | 0 | 3 | 2 | 0 |
| bounded-retry | PiAgent | yes | 231.036 s | 19,334 | 62,080 | 6,785 | 4,169 | 10 | 11 | 1 |
| bounded-retry | Structure | yes | 37.697 s | 3,158 | 640 | 1,093 | 0 | 3 | 2 | 0 |

Both surfaces passed both hidden verifiers, so both pairs are quality-comparable
inside this pilot. PiAgent/Structure geometric mean fresh-token ratio was
`4.4531`; mean duration ratio was `5.2967`. Structure won both paired fresh-token
comparisons. PiAgent's failed tools were guard/validation overhead: the tiny
fixtures intentionally have no configured package test script, and one
bounded-retry discovery command touched a protected `node_modules` pattern.

## Claim boundary

This is directional evidence, not a general agent ranking:

- there are only two synthetic JavaScript tasks and one repetition;
- PiAgent used the LongCat OpenAI Responses compatibility path while Structure
  currently used LongCat Chat Completions, so the model is matched but the wire
  dialect is not;
- PiAgent includes task contracts, policy guards, skills, provenance, and
  validation orchestration, while Structure used a deliberately small typed
  `read_file`/`write_file` surface;
- Structure's Chat Completions usage does not separately expose reasoning-token
  accounting, so its reported reasoning field is zero rather than a claim that
  the model performed no reasoning.

The result supports a narrower conclusion: for these two tasks, the corrected
PiAgent setup is reliable but substantially more expensive, while Structure's
smaller loop is faster and cheaper and now fails closed on the observed loop
class.

## Verification

- `cargo test --workspace --all-targets`
- `cargo fmt --all --check`
- `cargo clippy --workspace --all-targets -- -D warnings`
- four paid paired trials under
  `/tmp/structure-pi-vs-structure-20260824-1`

Raw provider exchanges and Pi runtime state remain in the private temporary
experiment directory because they can contain prompts, reasoning, and tool
output. Only aggregate evidence is committed.

## Expanded follow-up: three repeats plus broader tasks

The original two scenarios were extended to three independent repetitions, and
two additional scenarios were added: `stable-dedupe` exercises order,
SameValueZero equality, callback cardinality, and input immutability;
`config-batch` requires a coordinated two-file API change. This produced eight
paired runs and sixteen total paid trials.

| Aggregate | PiAgent | Structure |
|---|---:|---:|
| Strictly resolved | 7/8 | 8/8 |
| Hidden verifier passed | 8/8 | 8/8 |
| Timeout | 0 | 0 |
| Empty `toolUse` turns | 0 | 0 |
| Mean fresh tokens | 11,479 | 2,804 |
| Mean duration | 120.5 s | 25.0 s |
| Mean tool calls | 9.25 | 2.75 |
| Failed tool results | 20 | 0 |

Across the seven pairs where both surfaces strictly resolved, the geometric
mean PiAgent/Structure fresh-token ratio was `4.0120` and the mean duration
ratio was `4.2642`. Structure used fewer fresh tokens in every comparable pair.

The one PiAgent strict failure passed every functional verifier but added
`test/config.test.js` despite the prompt saying to edit only `src/config.js`.
PiAgent's generated task contract had automatically expanded its own scope to
`test/**`, `tests/**`, `spec/**`, and `__tests__/**`; the guard therefore allowed
the edit. The benchmark correctly retains this as a scope failure because the
user-authored constraint was narrower.

Most importantly for the original incident, none of the sixteen corrected
trials timed out and neither surface produced a `toolUse` turn without an
observed call item. The clean Git lifecycle fix therefore held across repeats
and the two broader tasks. This still remains a small controlled pilot, but it
now distinguishes the fixed loop class from a separate PiAgent scope-discipline
issue.
