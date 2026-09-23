# GLM-5.3-Flash FBGC campaign — 2026-08-29

## Campaign v3: retained negative pair

The first pre-registered `db-wal-recovery` block completed with real
GLM-5.3-Flash calls through the shared Responses-to-Chat adapter. Both arms
passed Harbor with reward 1.0. The pair is retained but is not pooled with
later code revisions.

- B0: 5 Provider calls, 5 tool calls, 14,763 total input, 10,944 cached input,
  3,819 uncached input, 2,454 output, and 1,394 reasoning output tokens.
- FBGC: 15 Provider calls, 16 tool calls, 108,847 total input, 86,400 cached
  input, 22,447 uncached input, 15,292 output, and 10,340 reasoning output
  tokens.
- Difference: FBGC used 18,628 more uncached input tokens and 10 more Provider
  calls. This is a clear negative sample, not evidence of savings.
- FBGC admitted one checkpoint at model step 8. Eight later Provider calls
  carried substitutive pointers. The first-admission and reuse-window gates
  passed, but no archive reread occurred, so the strict mechanism gate failed.

The Provider input fell from 8,589 tokens before admission to 6,517 immediately
after admission, confirming real wire substitution. Total cost still increased
because the stochastic FBGC trajectory used ten additional model turns.

## Root cause found in typed tool classification

The archived checkpoint contained three reasoning events and five shell tool
batches. Inspection of the exact archived canonical events exposed reversed
TTL protection caused by the Harbor runner's typed classifier:

- `which sqlite3; ... 2>/dev/null` was incorrectly classified as a mutation;
- real `cp`, `rm`, and `chmod` operations were not classified as mutations;
- an inline Python program that wrote `/app/main.db-wal` with
  `open(..., "wb").write(...)` was classified as inspection.

Consequently the FBGC checkpoint could archive the newly decoded WAL working
state while retaining less important probes. The model then performed repeated
checksum and database investigations. This bug is general to shell-backed
Structure runs; no fixture, prompt, verifier, Provider response, or competitor
was changed.

The classifier now treats `/dev/null` and descriptor routing as non-mutating,
recognizes common filesystem mutation commands, and detects mutating inline
Python programs. Focused regression tests and Clippy pass. Per the frozen
protocol, this implementation change ends campaign v3. Campaign v4 must rebuild
and rerun both B0 and FBGC; the v3 B0 result is not reused.

## Artifact locations

Private, git-ignored raw artifacts:

- `.local/benchmarks/formal-glm53flash-20260829-v3/long-horizon/manifest.json`
- `.local/benchmarks/formal-glm53flash-20260829-v3/long-horizon/jobs/primary-db-wal-recovery-b01-b0/`
- `.local/benchmarks/formal-glm53flash-20260829-v3/long-horizon/jobs/primary-db-wal-recovery-b01-fbgc/`

No API key or authorization header is present in these artifacts.

## Campaign v4: substitutive projection works, admission fails to adapt

The first `db-wal-recovery` pair passed Harbor in both arms. B0 used 9 Provider
calls, 8,054 uncached input tokens, and a peak request of 8,547 tokens. FBGC
used 9 Provider calls, 10,252 uncached input tokens, and a peak request of
7,046 tokens. FBGC admitted at the final request and the real Provider input
fell from 7,046 to 4,488 tokens (36.3%), confirming substitutive wire behavior.
Because only one post-admission request remained, the checkpoint could not
amortize its cache reset; this pair is negative on fresh input.

The first `build-cython-ext` pair also received Harbor reward 1.0 in both arms.
B0 terminated normally after 38 Provider calls, using 29,934 uncached input
tokens (656,366 total input) and peaking at 29,499 input tokens. FBGC reached
the agent time boundary after 51 calls without a final model message, using
64,871 uncached input tokens (838,055 total input) and peaking at 29,361 input
tokens. Under the stricter protocol the FBGC arm is unresolved despite the
verifier reward, so this is a quality and cost loss rather than a usable paired
cost sample. Different pre-admission model trajectories mean the extra calls
cannot be attributed to memory policy from this single stochastic pair.

FBGC admissions at steps 7 and 15 installed 8 and 16 new pointers. At step 36,
40 further safe batches could remove an estimated 14,753 tokens per call,
reset debt and cooldown were both clear, and 93 model steps remained;
nevertheless admission was rejected. The fixed 0.75 lifetime probability
always implied only about four future calls, ignoring the 35 continuations
already observed in this run.

The runtime now treats the configured 0.75 value as a prior and updates it
deterministically with observed continuation survival. The prior mass equals
the frozen minimum reuse window, preserving conservative early decisions while
allowing demonstrably long runs to checkpoint again. Focused and workspace
runtime tests and Clippy pass. This implementation change closes v4; no v4
result will be pooled with v5.

An invalid `build-cython-ext` B0 launch with misspelled CLI kwarg names was
stopped after two Provider calls and retained under an `invalid-` artifact
directory. It is excluded from all estimates. The valid paired arm started
from a fresh task container with the frozen kwarg names.

Private v4 artifacts are under
`.local/benchmarks/formal-glm53flash-20260829-v4/long-horizon/jobs/`.

## Campaign v5: adaptive survival works, first paid pair remains negative

The rebuilt binary has SHA-256
`6f36bf2a95db6a92cb1a71651f243fa82d467e9461f3bebf9e37a301af527764`.
The full workspace test suite, formatting check, and Clippy pass.

The first `db-wal-recovery` pair passed Harbor and terminated normally in both
arms. B0 used 6 Provider calls, 5,823 uncached input tokens, and 4,284 output
tokens. FBGC used 15 calls, 37,313 uncached input tokens, and 23,201 output
tokens. This is another clear negative paid sample.

The new telemetry confirms the adaptive calculation is active. At the step-8
admission, the configured 0.75 prior was updated from seven observed
continuations to an effective probability of 0.8666 and 7.4748 expected future
calls. Eight subsequent Provider requests carried pointers. The mechanism's
early-admission and reuse-window gates passed, but no archive reread occurred.
The arms had already diverged into radically different reasoning/output
trajectories before admission, so this pair does not isolate a causal context
effect. With no usable GLM seed support, the formal estimate still requires
many paired repeats and a bootstrap interval.

Private v5 artifacts are under
`.local/benchmarks/formal-glm53flash-20260829-v5/long-horizon/jobs/`.

### v5 deterministic context diagnostics

These diagnostic rows were later found to have used a stale pre-rebuild
`target/debug/tier_b` executable. They are retained as invalid infrastructure
evidence and excluded from every estimate. Campaign v7 below reruns the same
sweep from the rebuilt binary with an executable-visible Provider projection
metric.

The pre-existing deterministic Tier-B single-message fixture was run without
changing its prompt template, tools, or exact file oracle. Both B0 and FBGC
passed 5/5 at 12 and 24 tools with identical Provider-call and tool-call
counts. Provider-neutral input bytes were:

- 12 tools: B0 441,681; FBGC 431,017 (2.4% lower, 80 pointer appearances).
- 24 tools: B0 1,481,157; FBGC 1,392,581 (6.0% lower, 640 pointer appearances).
- 32 tools: B0 2,383,863; FBGC 2,196,788 (7.8% lower), but both arms failed
  because the fixture's existing 32-step limit leaves no step for the final
  completion message. This row is not quality-eligible.

The deterministic fixture Provider reports fixed synthetic token usage, so
only its serialized Provider-neutral request bytes are causal context evidence;
its token totals must not be interpreted as billing savings. The default
memory-recall fixture was also a correct FBGC no-op: both arms passed 5/5 and
FBGC admitted no pointers.

Inspection of the exact Provider projection found avoidable pointer overhead:
each typed pointer became a separate system message containing a repeated hash,
XML wrapper, and read instruction. Campaign v6 keeps the canonical pointer and
hash unchanged but emits one compact Provider-visible pointer index; hash
verification still occurs on `memory_read`.

## Campaign v7: deterministic FBGC ablation passes at long horizons

Campaign v7 reran the unchanged Tier-B single-message fixture at the complete
predeclared 12/24/32-tool scaling sweep. The rebuilt harness records both the
canonical typed `ModelRunRequest` bytes and the serialized typed Provider input
after Structure's adapter. All B0 and FBGC rows passed 5/5 with identical model
call and tool call counts.

Provider-projection bytes:

- 12 tools: B0 435,486; FBGC 415,461 (4.6% lower).
- 24 tools: B0 1,360,827; FBGC 1,174,258 (13.7% lower).
- 32 tools: B0 2,258,067; FBGC 1,838,310 (18.6% lower).
- Combined fixed sweep: B0 4,054,380; FBGC 3,428,029 (15.4% lower).

This is causal mechanism evidence: the deterministic Provider produced the
same trajectory in both arms, quality was identical, and only the context
policy differed. It supports “FBGC is better than B0 for sufficiently long
contexts” at the Provider projection layer. It is fixture evidence, not live
token billing evidence, and does not support the separate native-agent claim
against Codex CLI or Pi Agent.

Private v7 artifacts are under
`.local/benchmarks/formal-glm53flash-20260829-v7/deterministic-context/`.
# Related agent-stack results

The complete five-repeat v8 campaigns establish the separate native-stack
objective on the frozen small JavaScript suite:

- Codex CLI / Structure economic-token ratio: 2.8634x, paired bootstrap 95% CI
  [2.3042x, 3.5942x], with both surfaces resolving 20/20.
- Pi Agent / Structure economic-token ratio: 3.1691x, paired bootstrap 95% CI
  [2.4613x, 4.0178x]; Structure resolved 20/20 and Pi Agent 18/20.

See `reports/glm53flash-codex-structure-formal-v8-2026-08-29.md` and
`reports/glm53flash-piagent-structure-formal-v8-2026-08-29.md`. These short
agent tasks did not activate FBGC and therefore do not establish an FBGC causal
effect. The live long-horizon B0/FBGC campaign remains a separate requirement.

## Campaign v10: passthrough fixed, current admission policy fails

The host-side authenticated Chat passthrough was validated first with a real
GLM request and then with an unscored Harbor diagnostic. The diagnostic B0 arm
passed the task verifier with 7,334 uncached input tokens. The real credential
remained only in the proxy process; Harbor and all retained configuration used
the non-secret `local-proxy` client token.

Two frozen `db-wal-recovery` pairs then completed. Every arm passed Harbor and
Structure terminal verification, with no infrastructure failure or length
truncation:

- block 1: B0 7,939 versus FBGC 14,506 uncached input tokens;
- block 2: B0 6,701 versus FBGC 9,912 uncached input tokens.

Both FBGC mechanism gates failed. The first trajectory demonstrates that the
substitutive pointer projection itself works: Provider input fell from 11,246
tokens at call 6 to 3,331 at call 7. Admission nevertheless occurred only at
step 7/10 because the implementation waited for a complete eight-batch epoch,
and the FBGC trajectory used two more Provider calls plus substantially more
model output than its B0 pair. The later per-call context reduction could not
repay that divergence.

Campaign v10 was therefore stopped after four scored trials and sealed as a
failed pilot instead of spending the remaining 46 calls on a version that had
already failed its predeclared mechanism gate twice. The partial ledger, all
Harbor jobs, the interrupted fifth job, manifest, preflight, and checksums are
retained under
`.local/benchmarks/formal-glm53flash-20260829-v10/long-horizon/`. No v10 result
will be pooled into a later campaign.

## Campaign v11: the first epoch cap was ineffective

One frozen paid pair showed that the first epoch-cap implementation did not
change Harbor behavior. Both arms passed `db-wal-recovery`; B0 used 9,369 and
FBGC used 12,675 uncached input tokens. Runtime telemetry exposed the cause:
the Harbor minimum-reuse value was eight, so
`min(checkpoint_batches, minimum_reuse_steps)` remained eight. FBGC admitted at
step 8/11, carried pointers for four calls, and performed no archive hydration.
The mechanism gate failed and the pair is a cost loss.

V11 is closed and retained. The prospective v12 implementation instead caps a
FileBackedGC epoch at four independently atomic, verified batches. This number
comes from the pre-paid deterministic scaling sweep; the live pair did not
select it. The remaining cache-aware admission gates are unchanged, and v12
must use a newly rebuilt and checksummed binary.

## Campaign v12: context shrinks, GLM cache economics regress

The four-batch epoch cap activated as intended, but two paid pairs rejected the
current economics:

- `db-wal-recovery`: both arms passed; B0 used 8,008 fresh input tokens and
  FBGC 26,170. FBGC admitted at step 5/9 and exposed pointers for five calls.
- `build-cython-ext`: B0 passed with 33,928 fresh input tokens; FBGC failed the
  existing NumPy-alias/no-progress path with 49,550. It admitted at step 6/26
  and exposed pointers for 21 calls.

The long task isolates the mechanism/economics split. FBGC reduced total input
from 820,936 to 188,622 tokens (77.0%) and peak input from 32,603 to 13,674,
but B0 received 787,008 cached tokens while FBGC received only 139,072. Three
FBGC projection rewrites repeatedly invalidated GLM's prefix cache. The runtime
estimated savings from total-token density and charged only one preceding
cached count as reset debt, so it approved transformations that were beneficial
for context size but harmful for fresh-token cost.

V12 is closed and retained. Paid expansion is paused until the Structure
admission model values only estimated uncached savings and includes a
conservative cache warm-up cost. No task, prompt, verifier, model, or competitor
change is proposed.

## Prospective cache-aware economics correction

Structure now separates two quantities that v12 conflated:

- total tokens removed from the Provider context;
- estimated uncached tokens saved per later call, scaled by the run's
  cumulative Provider cache-hit fraction.

Only the second quantity can repay fresh-token reset debt. Once the Provider
reports a cache hit, the runtime remembers that capability across temporary
zero-cache responses and a projection rewrite reserves twice the latest full
request for conservative cache warm-up; a Provider that has never returned a
cache hit pays no invented reset penalty. A counterfactual replay of all v12 admission observations rejects
every harmful admitted checkpoint under this formula. For example, the WAL
decision falls from 2,783 apparent saved tokens per call to 1,515 estimated
fresh tokens, against 11,804 warm-up tokens, and is rejected.

The implementation adds explicit telemetry for total versus fresh estimated
savings and a regression test using the observed high-cache economics. All
workspace tests, Clippy, formatting, proxy tests, Python compilation, and diff
checks pass. No paid successor campaign has started yet.

## Prospective v11 change: bound checkpoint epoch by reuse horizon

The first attempted fix allowed any profitable partial FBGC epoch. It passed
task quality with identical deterministic call counts, but was rejected before
paid use because the unchanged 12-tool fixture increased Provider projection
bytes by 0.7% and weakened the longer-horizon reductions.

The selected implementation retains complete epochs and instead caps the FBGC
epoch at `min(checkpoint_batches, minimum_reuse_steps)`. Under the frozen
8-batch/4-call settings this admits four batches at a time. It does not change
PointerGC or any cache, profitability, TTL, archive, or projection gate.
Deterministic 5-repeat results on the unchanged single-message fixture are:

- 12 tools: B0 435,486 versus FBGC 415,461 Provider bytes (4.6% lower);
- 24 tools: B0 1,360,827 versus FBGC 1,174,258 (13.7% lower);
- 32 tools: B0 2,258,067 versus FBGC 1,838,310 (18.6% lower).

Every arm passed 5/5 with identical Provider and tool-call counts. The complete
workspace test suite passes. These are provider-neutral diagnostics, not live
token claims; v11 requires a rebuilt binary and new paid artifacts.

## Campaign v13: cache-aware admission safely stays inactive

The first paid pair after the cache-aware correction used the unchanged
`db-wal-recovery` task. Both arms passed. B0 used 11,297 fresh input tokens
across nine Provider calls; the FBGC arm used 7,254 across seven calls. This is
not counted as an FBGC token win: FBGC admitted no checkpoint and emitted no
pointer, so the lower total came from an independently shorter stochastic
trajectory and fails the predeclared mechanism gate.

The inactive mechanism is nevertheless useful safety evidence. At model step
6 the candidate offered 1,684 total tokens but only 724 estimated fresh tokens
per later call against 11,364 tokens of cache-reset debt; at step 7 it offered
3,055 total and 1,014 fresh against 12,782 reset tokens. Both candidates were
rejected. This demonstrates that the v12 cache-destroying transformations are
no longer admitted on the same high-cache Provider. V13 is closed and retained
under `.local/benchmarks/formal-glm53flash-20260829-v13/long-horizon/`; it is
not pooled with any successor.

GLM's official documentation says cached input is normally priced at 50% of
standard input. Future summaries therefore retain three separate views: the
frozen primary fresh-input lower bound (cache weight zero), the Provider's
typical price-weighted input (cache weight 0.5), and total input (cache weight
one). The additional views cannot override the original primary gate. Source:
<https://docs.bigmodel.cn/cn/guide/capabilities/cache>.

## Prospective v14 working-set protection amendment

The v12 `build-cython-ext` failure also exposed a quality hazard independent of
cache economics: the existing working-state guard pinned recent failed tool
batches but allowed a newly successful inspection to be archived immediately.
One archived inspection contained the NumPy-alias evidence needed by the next
step. Pointer hints deliberately contain protocol metadata only, so copying
paths, arguments, or result fragments into them would weaken the established
privacy boundary.

The prospective Structure-only correction pins the two most recent successful
tool batches classified by the runner as `Inspection`, alongside the existing
two recent error batches. It does not pin mutation, build, dependency, or
validation batches and does not change Event Log contents, pointer payloads,
tasks, prompts, tools, model, verifier, or competitors. Focused tests prove
that only the newest successful inspections are selected, failed inspections
remain governed by the error policy, and mutation batches are not selected.

The unchanged deterministic 12/24/32-tool sweep remains 5/5 for both arms with
identical call counts. FBGC reduces Provider projection bytes by 4.6%, 13.7%,
and 18.6%, respectively. Artifacts are under
`.local/benchmarks/diagnostic-glm53flash-20260829-v14-working-set/`. These are
non-network mechanism checks, not paid claims. Any v14 paid run requires a new
release build, checksums, preflight, and campaign directory.

## Campaign v14: working-set fix passes the task oracle but FBGC remains inactive

The unchanged `build-cython-ext` block-1 pair was run from the frozen v14
binary. Harbor's hidden repository verifier awarded both arms 1.0. Under the
stricter predeclared quality rule, B0 passed while FBGC failed: the FBGC agent
had already produced a verifier-passing repository but continued optional
inspection until Runtime terminated it after 12 consecutive model steps with
no successful state-changing tool. The working-set protection therefore fixed
the lost-evidence failure mode, but not the separate failure-to-stop mode.

FBGC made 42 Provider calls, used 439,700 total / 418,496 cached / 21,204 fresh
input tokens, and never admitted a checkpoint. B0 made 49 calls and used
790,894 total / 756,480 cached / 34,414 fresh. The apparent FBGC reductions
cannot be attributed to compaction because the mechanism was inactive; they
come from the shorter stochastic trajectory. The mechanism gate and strict
quality gate both fail, so v14 is not an FBGC win and is closed after this pair.

The no-admission telemetry exposes a metric boundary. At model step 41 FBGC
could remove an estimated 10,609 total input tokens per later call, but the
cumulative cache-hit ratio reduced the fresh-only estimate to 524, against
36,496 tokens of conservative cache-recovery debt. With nearly perfect prefix
cache reuse, deleting old cached tokens reduces total context and the
Provider's real 50%-weighted bill, but cannot create a continuing reduction in
the zero-price-cache metric `input - cached`; it instead incurs a one-time
fresh reset. The frozen fresh metric is retained and not reinterpreted.

Artifacts are retained under
`.local/benchmarks/formal-glm53flash-20260829-v14/long-horizon/`. The FBGC arm's
external verifier result and agent terminal status must be reported separately;
the strict `verifier_passed` field remains false because the protocol requires
both.

## Prospective v15 completion advisory

Structure now records a typed `agent.progress.advisory` event after six
consecutive model steps without a successful state-changing tool. Its projected
message asks the model to return the final answer if requirements are already
satisfied, otherwise to choose one state-changing action rather than continue
optional investigation. It does not fabricate success, weaken the 12-step
termination guard, change tool output, inspect task-specific content, or alter
the Event Log's lossless evidence.

The Harbor ledger also records `external_verifier_passed` and
`agent_terminal_success` separately while keeping `verifier_passed` as their
strict conjunction. Its length-truncation parser now reads the canonical Event
payload shape. Focused runtime/protocol tests and Python compilation pass; a
new full verification and frozen binary are required before any paid v15 run.

## Prospective v15 Provider-price-aware admission amendment

The Runtime admission model now accepts an explicit cached-input price in basis
points. Its default is zero, which reproduces the frozen fresh-only policy.
GLM experiments may set 5,000 bps, matching the Provider's documented typical
50% cache price. Runtime records all three quantities independently:

- total context removed per future call;
- fresh-only savings, with cached input valued at zero;
- price-weighted savings used for admission.

Cache-reset debt is multiplied by the uncached price premium. Thus a 5,000-bps
cached-input price values both the continuing removal of cached context and the
temporary loss of its discount at one half of standard input price. No field is
renamed and the fresh-only result remains available as a deliberately harsher
counterfactual.

The setting is explicit from the v4 frozen manifest through the serial Harbor
runner and Python bridge into `CoreRuntime`; it is not inferred from task names
or outputs. A regression test uses one high-cache state to prove that the
fresh-only calculation remains 250 saved tokens while the half-price economic
calculation is 625, and that only the latter can repay a 4,000-token weighted
reset under the stated horizon. A dry run confirms the unchanged
`build-cython-ext` FBGC trial receives exactly `pgc_cached_input_cost_bps=5000`.
Paid use still requires full validation, a release build, checksums, proxy
preflight, and a new campaign directory.

## Campaign v15: substitutive FBGC is active, but the frozen strict gate stops expansion

The frozen `build-cython-ext` block-1 pair ran on 2026-08-31 with the same
model, task, seed, prompt, tools, proxy, and limits. FBGC ran first as declared.
There was no infrastructure failure or output-length truncation.

FBGC produced a repository that passed all 11 hidden tests; B0 passed 8/11 and
left three Python/NumPy compatibility failures. Neither arm returned a final
text response. Both eventually hit the generic 12-step no-state-progress
guard, so the frozen strict `external verifier AND agent terminal` field is
false for both arms. Expansion therefore stops after this pair.

The FBGC mechanism was genuinely active: four admissions occurred at model
steps 6, 14, 24, and 39; pointers appeared in 47 Provider calls; and all four
pointer transitions reduced continuation bytes while increasing pointer
entries. This is direct evidence of substitutive rather than additive
projection. FBGC made 52 Provider calls versus B0's 34, yet reduced total input
from 460,190 to 365,457 (-20.59%), half-price weighted input from 244,798 to
207,729 (-15.14%), and peak input from 29,266 to 11,645. Fresh input increased
from 29,406 to 50,001 (+70.04%) because each projection rewrite loses part of
the cached prefix. Output tokens were 13,364 versus 12,394.

The old mechanism gate still failed because it required a post-admission
archive read. No such read occurred: the model completed the repository without
needing older archived evidence. This condition tests optional model behavior,
not whether the pointer was projected substitutively or whether the archive is
recoverable. V15 artifacts remain unchanged under
`.local/benchmarks/formal-glm53flash-20260829-v15/long-horizon/` and are not
re-scored under a successor rule.

## Prospective v16 generic terminal and mechanism amendment

Structure now exposes a typed `runtime_complete` tool. It must be the sole tool
call in a response and requires a non-empty status. Runtime records the original
model response and the requested/classified/completed tool events before
emitting `run.completed`; it never routes this call through the shell runner.
The Harbor prompt now asks every arm to use this generic terminal protocol after
requirements and validation are complete. This addresses GLM's observed
tendency to keep selecting tools under `tool_choice=auto` without fabricating
success or inspecting fixture-specific output.

The successor mechanism gate retains early admission and a minimum post-
admission reuse window, but directly requires pointer-bearing Provider calls
and at least one transition where pointer entries increase while exact
continuation bytes decrease. Archive rereads remain reported but are no longer
mandatory when the task does not need old evidence. The Harbor report schema is
`structure.harbor-agent/v11`. Runtime tests (69/69), Harbor-agent tests (11/11),
the full workspace suite, formatting, and workspace Clippy all pass. No v16
paid result exists until a new release binary, manifest, checksums, and
preflight are frozen.

## Campaign v16: terminal protocol works, FBGC quality still fails

V16 reused the v15 manifest byte-for-byte. B0 used `runtime_complete` after its
validation and passed all 11 external tests, proving the new terminal protocol
works on the actual Provider path. FBGC never called it, hit the no-progress
guard at model step 24, and passed only 2/11 external tests because it built the
source tree in place but never installed `pyknotid` into the global Python
environment. The strict pair therefore fails and v16 stops after one pair.

FBGC's revised mechanism gate passed: admissions occurred at steps 6, 14, and
22; pointer-bearing requests persisted for 20 calls; and three pointer-growth
transitions reduced continuation bytes. It used 170,173 total / 115,648 cached /
54,525 fresh / 112,349 half-price weighted input tokens over 25 calls. B0 used
648,883 / 619,264 / 29,619 / 339,251 over 39 calls. The large total and economic
reduction is invalid as a win because FBGC quality is worse; fresh input is
also worse.

The retained trajectory shows that FBGC's last no-progress window contained
repeated tool errors, whereas B0's clean validated window correctly ended via
`runtime_complete`. The single generic advisory had treated these situations
the same. The successor implementation now counts tool errors inside the
current no-progress streak. At six steps, a clean window recommends
`runtime_complete`; an error-bearing window explicitly forbids completion and
asks for one corrective mutation, dependency, or build action before required
validation. The hard 12-step stop is unchanged. This is task-neutral Runtime
control and applies identically to all arms.

## Campaign v17: external quality recovers, but the terminal gate still stops expansion

V17 reused the v16 manifest byte-for-byte and changed only the generic,
error-aware advisory described above. The frozen first pair completed without
infrastructure failure or length truncation. Both arms passed all 11 external
tests. B0 called `runtime_complete`, so its strict quality gate passed. FBGC
completed the repository correctly but never called `runtime_complete`; it hit
the 12-step no-state-progress guard at model step 51. Its strict quality gate
therefore fails and v17 stops after one pair.

FBGC's mechanism gate passed with four admissions, 47 pointer-bearing Provider
calls, four substitutive pointer transitions, and two post-admission archive
reads. Against B0 it reduced total input from 908,076 to 515,740 (-43.21%),
half-price weighted input from 472,684 to 309,116 (-34.60%), output from 19,309
to 17,947 (-7.05%), and peak input from 37,209 to 17,227 (-53.70%). It increased
fresh input from 37,292 to 102,492 (+174.84%), Provider calls from 39 to 52, and
tool calls from 52 to 64. These are not accepted as an FBGC win because strict
quality failed and the trajectory was longer.

The retained typed events isolate a second-window control bug. At model step
45, the six-step advisory correctly observed four tool errors and requested a
corrective action. The model then performed successful corrective mutations
and validation, but the advisory is emitted only when the whole no-progress
streak first equals six. At streak 12 Runtime terminated immediately, so it
never emitted a clean-window completion advisory after recovery. A successor
must evaluate consecutive advisory windows independently and give a recovered,
clean window one opportunity to call `runtime_complete`; v17 artifacts and
scores remain unchanged.

## Prospective v18 windowed recovery advisory

Runtime now measures tool errors in independent six-step no-progress windows.
It emits a new advisory only when the window state changes between error-bearing
and clean. If a clean window follows an error-bearing window exactly at the
hard no-progress threshold, Runtime projects that clean completion advisory and
allows one additional model turn; failure to complete on that turn terminates
normally. State-changing progress resets the streak and all window state. This
uses only typed tool outcomes and applies identically to B0 and FBGC.

A regression test executes six failing validations followed by six clean
validations at a 12-step limit and proves the model receives one final
`runtime_complete` turn. Runtime tests pass 71/71, and the full workspace test
suite, formatting, and Clippy with warnings denied pass. No v18 paid result
exists until a new release binary and campaign freeze are created.

## Campaign v18: window logic is valid, but this paid trajectory fails earlier

V18 was frozen with the manifest byte-identical to v17 and passed all preflight
checks. The first pair completed without infrastructure failure or truncation.
FBGC passed only 2/11 external tests: it cloned the repository but did not leave
a globally importable built package. Its clean advisory appeared at model step
19, was not followed by completion or state change, and the unchanged hard
guard terminated it at step 25. The new recovery grace was not applicable
because there was no error-bearing-to-clean window transition. B0 passed 11/11
external tests but also ignored two error-bearing advisories and terminated at
step 36 without `runtime_complete`. Both strict gates fail, so v18 stops after
one pair.

FBGC's mechanism gate passed with three admissions, 21 pointer-bearing calls,
and two substitutive transitions. It used 230,584 total / 173,696 cached /
56,888 fresh / 143,736 half-price weighted input over 26 Provider calls, with a
peak of 18,112. B0 used 519,381 / 493,184 / 26,197 / 272,789 over 37 calls, with
a peak of 26,699. FBGC reduced total input 55.60%, half-price input 47.31%, peak
input 32.16%, and calls 29.73%, but increased fresh input 117.15%. None of these
is a win because FBGC quality is lower and neither arm passed the terminal gate.

V17 and v18 together show high trajectory variance under the same seed at the
remote sampling boundary: v17 FBGC passed 11/11, while v18 FBGC passed 2/11.
They also show that a one-shot natural-language completion advisory is not a
reliable terminal protocol for this Provider. No further paid expansion is run
from v18.
