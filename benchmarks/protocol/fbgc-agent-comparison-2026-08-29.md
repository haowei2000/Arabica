# FBGC and agent comparison protocol — 2026-08-29

Status: frozen before the first formal run. Earlier pilots are mechanism and
infrastructure evidence only and are excluded from the formal estimates.

## Claims kept separate

1. **Context-policy claim:** Structure FBGC improves fresh input cost relative
   to Structure B0 full replay without worse task quality.
2. **Agent-stack claim:** the complete native Structure agent has an overall
   quality/cost advantage over the complete native Codex CLI and Pi Agent
   stacks. This is two pairwise comparisons, never a pooled ranking.

No fixture, prompt, verifier, competitor source, or competitor configuration
may be changed in response to an observed result. Provider compatibility work
may translate wire protocols losslessly and must be shared by all affected
arms. General Structure implementation changes are allowed only between
versioned campaigns; after such a change, all arms in the affected campaign
are rebuilt and rerun.

## Frozen treatments

### Long-horizon B0/FBGC

- Tasks: all five pre-existing `QUALIFICATION_CANDIDATES` in
  `long_horizon.rs`; no post-hoc task selection.
- Model/provider: GLM-5.3-Flash through the same upstream credential and
  provider adapter.
- Five paired blocks per task. Treatment order is deterministically balanced;
  both arms in a block receive the same sampling seed.
- Same Rust binary, task image, user instruction, system prompt, tools, limits,
  timeout, and verifier. The only treatment is B0 versus FBGC memory policy.
- All attempts and infrastructure failures remain in the ledger.

### Repository context-policy A/B

- The existing four-scenario `benchmarks/cli-comparison/suite.json` tree is
  frozen by its manifest digest.
- Five repeats per scenario, comparing exactly `structure-full-replay` with
  `structure-file-backed-gc` through the same execution function.
- Provider requests and FBGC archive artifacts are retained privately; no
  authorization headers or credentials are captured.

### Native agent-stack comparisons

- Two manifests: Structure versus official open-source Codex CLI, and
  Structure versus the pinned open-source Pi Agent checkout.
- Five repeats of every frozen repository scenario.
- Each agent keeps its native system prompt, tool/MCP surface, loop, and context
  implementation. Task, model request, timeout, allowed changes, and verifier
  are identical.
- Codex runs with an isolated home and no personal configuration. Pi Agent uses
  its official project bootstrap in a fresh Git repository. Executable version,
  source revision, and checksums are saved with campaign artifacts.

## Metrics and decision rules

Quality is the primary hard gate. A trial resolves only when the process exits
normally, does not time out, passes the hidden verifier and output markers, and
changes no file outside the allowed scope.

For long-horizon FBGC:

- quality non-inferiority margin: 10 percentage points in pass rate;
- cost population: only paired blocks where both treatments resolve without an
  infrastructure failure;
- at least five eligible pairs;
- the FBGC trajectory/mechanism gate must pass;
- geometric-mean FBGC/B0 uncached-input ratio must show at least a 10% reduction;
- a deterministic 10,000-resample paired-bootstrap 95% confidence interval must
  lie wholly below 1.0.

For repository context-policy, the paired cost is fresh input only
(`input - cache_read` in the canonical OpenAI accounting). Model output is
reported separately and cannot create a context-savings win. For agent-stack,
the paired economic cost is fresh input plus output. In either mode a token
winner requires at least five double-success pairs, non-inferior resolved count,
and the 10,000-resample paired-bootstrap 95% interval wholly on its side of 1.0.
Every double-success context-policy pair must also show both an admitted FBGC
decision and a subsequent Provider call carrying a memory pointer; otherwise
the context-policy mechanism gate fails and no FBGC winner is declared.

Secondary metrics never override the quality and token gates: cache hit/write,
total input, output, reasoning output, Provider/model calls, tool calls,
duration, failed tools, protocol errors, repeated no-progress turns, and
timeouts. Report point estimates, pair counts, confidence intervals, every
failure, and missing data.

“Structure exceeds Codex CLI and Pi Agent” is supported only if Structure is
the formal winner in both separate native agent-stack summaries. A B0/FBGC win
does not count as an agent-stack win, and vice versa.

## Artifact and amendment policy

Each campaign directory contains the immutable manifest, suite digest, source
revision and dirty-tree digest (if any), executable versions/checksums, ordered
trial reports, raw Provider exchanges where supported, partial timeout usage,
verifier output, and generated summary. Secrets and authorization headers are
never written.

Any change after the first formal call requires a dated amendment and a new
campaign identifier. Results before and after an implementation or protocol
change are not pooled.

## Amendments

- **2026-08-29, campaign v3 closed:** the first real paired result exposed a
  generic shell interaction classification bug that reversed TTL protection
  for `/dev/null` routing versus actual filesystem/Python mutations. The
  classifier was corrected without changing fixtures, prompts, verifiers, or
  competitors. Campaign v4 reruns both arms from a rebuilt binary; no v3 result
  is pooled into v4.
- **2026-08-29, campaign v4 closed:** real `db-wal-recovery` and
  `build-cython-ext` FBGC trajectories showed that the configured continuation
  probability remained a fixed lifetime estimate even after the run had
  produced many observed continuations. On the 51-call build trajectory this
  caused a profitable third checkpoint to be rejected: the estimate remained
  about four future calls although more than twenty continuations had already
  occurred since the preceding admission. The configured probability is now
  treated as a deterministic beta-prior mean and updated from this run's
  observed continuations. Fixtures, prompts, verifiers, competitors, and
  frozen manifest parameters remain unchanged. Campaign v5 rebuilds and reruns
  both arms; v4 results are retained but never pooled into v5.
- **2026-08-29, campaign v5 closed:** deterministic provider-neutral scaling
  showed that canonical pointers were still projected as one verbose system
  message per archive, redundantly repeating the content hash, XML wrapper,
  and memory-read instruction. Event Log and typed pointer records remain
  unchanged, but the Provider projection now combines all visible pointers
  into one compact JSON index. It exposes the recoverable path, kind, event
  count, and semantic hint; archive hash verification remains internal to
  `memory_read`. No fixture, prompt, oracle, or competitor changed. Campaign v6
  rebuilds and reruns all affected arms; v5 is retained and not pooled.
- **2026-08-29, campaign v6 closed:** the existing Tier-B byte metric was
  confirmed to serialize the pre-adapter `ModelRunRequest`, so it correctly
  measured typed canonical projection size but could not observe compact
  pointer aggregation at the Provider boundary. A parallel, non-replacing
  `provider_projection_bytes` metric now serializes the typed Provider input
  after Structure memory projection. Provider-reported tokens remain
  authoritative for live economics. Campaign v7 reruns the full deterministic
  scaling sweep with both byte layers; v6 is not pooled.
- **2026-08-29, Codex agent-stack campaign v7 closed before ranking:** the GLM
  Responses-to-Chat adapter rejected Codex's default `multi_agent_v1`
  namespace and provider-hosted `web_search` tool. Disabling those Codex
  features produced real but invalid diagnostic results and is forbidden for
  ranking. The adapter now encodes namespace children as collision-resistant
  Chat function names and restores the exact `namespace` and child `name` in
  Responses output. It advertises `web_search`; if GLM selects it, the trial is
  retained as an infrastructure failure because Chat Completions cannot execute
  a provider-hosted search. No Codex option, fixture, prompt, verifier, or
  Structure treatment is changed. A native Codex text turn and a complete
  fixture tool loop passed with the default namespace and web-search surface
  present. Campaign v8 rebuilds the harness and begins from a new manifest.
- **2026-08-29, artifact amendment for campaign v8:** every trial now persists
  raw process stdout and stderr beside the unscored workspace and records both
  relative paths and SHA-256 digests in its report. This closes an audit gap;
  it does not change model input, tools, task execution, verification, or cost
  accounting. Earlier reports retain only hashes and are not promoted to v8.
- **2026-08-29, native agent-stack campaign v8 completed:** both complete
  five-repeat manifests finished without post-freeze changes. Structure was the
  formal winner against Codex CLI and Pi Agent in their separate summaries.
  These results close only the native agent-stack objective; the short tasks did
  not activate FBGC and are not context-policy evidence.
- **2026-08-29, long-horizon campaign v9 orchestration amendment:** the frozen
  long-horizon manifest generator previously required manual Harbor commands
  and had no executable ledger path. The generic
  `benchmarks/harbor/run_long_horizon.py` runner now materializes one Harbor
  JobConfig per scheduled trial, binds the manifest's exact agent timeout,
  executes trials serially in manifest order, preserves complete Harbor job
  directories, and derives the typed summary ledger from Harbor reward plus
  Runtime terminal status. It receives credentials only through inherited
  environment variables and never serializes them. Dry-run Harbor config
  validation and extraction against retained v5 artifacts passed. This changes
  no task, prompt, verifier, provider request, tool, memory policy, or agent
  code. Long-horizon v9 starts from a rebuilt binary and a new manifest; no
  earlier paid result is pooled.
- **2026-08-29, long-horizon campaign v9 closed after infrastructure failure:**
  the first B0 and FBGC jobs both failed before any Provider call because Harbor
  0.20.0 removes sensitive parent-process environment variables from the custom
  agent phase. The Rust bridge therefore received no API key. Both zero-token
  attempts and exception traces are retained; the remaining schedule was
  stopped rather than repeating the same failure. A v10 compatibility path uses
  a localhost Chat passthrough: the real GLM key remains only in that process,
  while Harbor serializes only a non-secret localhost token and URL. The Python
  bridge passes those explicit non-secret values to the Rust process. This is
  shared infrastructure for both arms and changes no model request body,
  fixture, prompt, verifier, tool, or memory policy. Campaign v10 may begin only
  after a real Chat passthrough smoke succeeds and all adapter/bridge/runner
  checksums are frozen.
- **2026-08-29, long-horizon campaign v10 closed as a failed four-trial
  pilot:** the authenticated localhost passthrough and complete Harbor chain
  passed a real diagnostic, then two frozen `db-wal-recovery` pairs completed
  without infrastructure failures. All four arms passed the task verifier.
  FBGC used 14,506 versus 7,939 and 9,912 versus 6,701 uncached input tokens;
  both FBGC mechanism gates failed. Provider telemetry proves substitutive
  projection worked (one trajectory fell from 11,246 input tokens before
  admission to 3,331 after admission), but the first checkpoint was delayed
  until model step 7 because eight eligible batches were required. Additional
  model calls and output variance outweighed the later per-call reduction.
  The remaining schedule was stopped rather than spending 46 paid trials on a
  version that had already failed its predeclared mechanism gate. The four
  results and the interrupted fifth job are retained and will never be pooled
  with a successor campaign. A successor may change only Structure's generic
  admission implementation, must use a new campaign identifier and rebuilt
  binary, and must keep the frozen tasks, prompts, tools, limits, model, and
  verifier unchanged.
- **2026-08-29, prospective v11 FBGC admission amendment:** v10 showed that
  the configured eight-batch checkpoint could exceed the four-call minimum
  reuse horizon and delay the first useful substitution until step 7/10.
  Structure now caps only FileBackedGC's complete checkpoint epoch at the
  smaller of the configured checkpoint size and configured minimum reuse
  window. PointerGC keeps its prior epoch semantics; profitability, measured
  cache-reset cost, continuation posterior, reset debt, cooldown, TTL safety,
  archive verification, and substitutive projection are unchanged. An
  alternative implementation that admitted arbitrary partial epochs was
  rejected before any paid v11 call because the unchanged deterministic
  12-tool fixture regressed Provider bytes. The selected complete-epoch rule
  passes all workspace tests and reproduces 5/5 quality with identical call
  counts at 12, 24, and 32 tools while reducing Provider projection bytes by
  4.6%, 13.7%, and 18.6%. V11 must rebuild and freeze a new binary and may not
  pool v10 trials.
- **2026-08-29, campaign v11 closed after one paid pair:** both arms passed
  `db-wal-recovery`, but FBGC used 12,675 versus B0's 9,369 uncached input
  tokens and its mechanism gate failed. The admission telemetry showed that
  Harbor's Runtime minimum-reuse setting was eight, so the prospective
  `min(checkpoint_batches, minimum_reuse_steps)` rule remained eight and made
  no behavioral change. This pair is retained and not pooled. V12 replaces
  that ineffective rule with a Structure-wide maximum FBGC epoch of four
  independently atomic file-backed batches. The choice was established by the
  unchanged deterministic 12/24/32-tool sweep before v12 paid calls; all
  existing profitability, cache-reset, debt, cooldown, TTL, and verification
  gates remain active. Tasks, prompts, tools, limits, Provider, and verifier
  remain frozen.
- **2026-08-29, campaign v12 closed after two diagnostic pairs:** the four-batch
  epoch worked mechanically. On `db-wal-recovery`, FBGC admitted at step 5/9
  and carried pointers for five calls, but used 26,170 versus B0's 8,008 fresh
  input tokens. On `build-cython-ext`, FBGC admitted at step 6/26 and carried
  pointers for 21 calls; it failed the verifier on the existing NumPy alias
  issue and no-progress guard while B0 passed. FBGC nevertheless reduced total
  input from 820,936 to 188,622 and peak input from 32,603 to 13,674 tokens,
  proving the context substitution itself is effective. It lost the fresh-token
  metric because three prompt rewrites destroyed GLM prefix-cache reuse: B0
  reported 787,008 cached tokens versus FBGC's 139,072. The profitability model
  incorrectly valued removal using total-token density and treated only the
  preceding cached count as a one-call reset cost. V12 is retained and not
  pooled. No further paid campaign may start until Structure estimates savings
  in uncached-token terms and conservatively accounts for cache warm-up after a
  projection rewrite.
- **2026-08-29, prospective cache-aware economics amendment:** Structure now
  records total estimated context removal separately from estimated uncached
  savings. Admission uses only the latter, scaled by the cumulative Provider
  cache-hit fraction for the run. Once a real cache hit is observed, that fact
  survives temporary zero-cache responses after a rewrite; reset debt includes
  twice the latest full request as a conservative cache warm-up. When the
  Provider has never reported cache reuse, no synthetic reset penalty is added. Replaying
  every v12 candidate decision through the new formula rejects all three
  harmful admissions and every pending candidate. Focused regression tests,
  the full workspace suite, formatting, Clippy, adapter tests, and Python
  compilation pass. This is a Structure-only economic correction; it does not
  change fixtures, prompts, tools, limits, Provider, verifier, or competitors.
  It has not yet been used for a paid campaign and must receive a new campaign
  identifier and binary checksum before doing so.
- **2026-08-29, campaign v13 closed after one paid safety pair:** both arms
  passed `db-wal-recovery`; B0 used 11,297 fresh input tokens over nine calls
  and FBGC used 7,254 over seven. This is not an FBGC win because the FBGC arm
  admitted no checkpoint and emitted no pointer, so its shorter trajectory
  fails the mechanism gate. The cache-aware policy rejected candidates at
  steps 6 and 7: estimated fresh savings were 724 and 1,014 tokens per future
  call against 11,364 and 12,782 tokens of cache-reset debt. V13 is retained as
  safety evidence that the harmful v12 rewrites are suppressed and is not
  pooled. GLM's official cache documentation states that cached input is
  normally priced at 50% of standard input; future reports add that weighted
  view and total input as secondary metrics while preserving fresh input as the
  frozen primary gate.
- **2026-08-29, prospective v14 working-set amendment:** the v12 long-task
  failure showed that a newly successful inspection could be archived while
  recent errors were pinned. Structure now protects the two most recent
  successful batches carrying the typed `Inspection` classification. It keeps
  pointer hints metadata-only and does not protect mutations, builds,
  dependencies, or validations. The change is generic runtime policy derived
  from the retained failure, not task- or content-specific logic. Focused tests
  cover recency, success/error separation, and exclusion of mutation batches.
  The unchanged deterministic 12/24/32-tool sweep remains 5/5 in both arms
  with identical calls and the same 4.6%, 13.7%, and 18.6% FBGC Provider-byte
  reductions. No paid v14 call may run before a new binary/checksum/preflight.
- **2026-08-29, campaign v14 closed after one `build-cython-ext` pair:** the
  hidden repository verifier awarded both arms 1.0, but the strict protocol
  passed only B0. FBGC had already produced the correct repository, then
  continued optional checks until the 12-step no-state-progress guard fired.
  It used 439,700 total / 418,496 cached / 21,204 fresh input tokens over 42
  calls; B0 used 790,894 / 756,480 / 34,414 over 49. FBGC admitted no checkpoint
  and emitted no pointer, so none of those apparent reductions are attributed
  to compaction. At step 41 it could remove 10,609 total tokens per call but
  only 524 under the zero-price-cache fresh metric, against 36,496 of cache
  recovery debt. V14 fails both strict quality and mechanism gates and is not
  pooled with a successor.
- **2026-08-29, prospective v15 completion-control amendment:** after six
  consecutive steps without successful state change, Runtime appends a typed,
  protected `agent.progress.advisory` telling the model to finish if all task
  requirements are met, or otherwise make one state-changing action. It neither
  claims success nor weakens the existing 12-step hard stop, and it is generic
  across tasks and strategies. Harbor reporting now separates external verifier
  success from agent terminal success while preserving their conjunction as
  the strict quality field; canonical nested run-failure payloads are also used
  for truncation detection. No paid v15 trial may start before full validation,
  rebuild, checksum, and preflight.
- **2026-08-29, prospective v15 price-aware admission amendment:** PointerGC
  admission now receives an explicit cached-input cost in basis points. Zero is
  the default and preserves the original fresh-only behavior; the new v4 GLM
  manifest declares 5,000 bps from the Provider's published typical half-price
  cache policy. Telemetry keeps total removal, fresh-only savings, and
  price-weighted savings as separate fields. Reset debt is charged only for the
  uncached price premium. The value flows through manifest, serial Harbor
  runner, bridge, and Runtime without inspecting task content. Regression tests
  prove zero-price behavior is unchanged and half-price arithmetic is explicit;
  a dry run confirms the frozen FBGC job receives 5,000 bps. This adds a real
  economic decision view but does not delete or relabel the frozen fresh metric.
- **2026-08-31, campaign v15 closed after one `build-cython-ext` pair:** FBGC
  passed the external verifier 11/11 while B0 passed 8/11, but neither arm
  emitted a successful terminal response; both hit the same 12-step no-state-
  progress guard. The frozen strict quality gate therefore fails and expansion
  stops. FBGC admitted at steps 6/14/24/39, projected pointers in 47 calls, and
  had four pointer-growth transitions with lower continuation bytes, proving
  substitutive projection was active. It reduced total input 20.59%, official
  half-price weighted input 15.14%, and peak input 60.21%, but increased fresh
  input 70.04% due to cache-prefix rewrites. The old archive-reread mechanism
  condition also failed because the completed task never needed archived
  evidence. V15 remains frozen and is not retroactively re-scored.
- **2026-08-31, prospective v16 generic terminal/mechanism amendment:** Runtime
  adds a typed `runtime_complete` tool that is accepted only as a sole tool call
  with a non-empty summary. Its model response and tool lifecycle remain in the
  canonical Event Log; the shell runner is not invoked. The same tool and
  instruction apply to B0 and FBGC. The successor mechanism gate directly
  requires pointer-bearing Provider calls plus a pointer-growth transition that
  reduces exact continuation bytes. Archive reread stays observable but is not
  required when no old evidence is needed. This is a generic Structure protocol
  correction derived from v15, not a task, fixture, verifier, or competitor
  change. A new campaign ID and frozen artifacts are mandatory before paid use.
- **2026-08-31, campaign v16 closed after one `build-cython-ext` pair:** the
  byte-identical v15 schedule was used. B0 called the new `runtime_complete`
  tool and passed 11/11 external tests. FBGC's mechanism gate passed with three
  admissions, 20 pointer-bearing calls, and three substitutive transitions, but
  it built only in place, never installed the package globally, passed 2/11,
  and hit no-state-progress at step 24. Its lower total and half-price input is
  not a win because quality is worse; its fresh input was also higher. V16
  stops and is not expanded.
- **2026-08-31, prospective error-aware advisory amendment:** Runtime now
  counts tool errors only inside the current no-state-progress streak. A clean
  six-step window recommends `runtime_complete`; an error-bearing window says
  required validation is not complete, forbids completion, and requests one
  corrective mutation, dependency, or build action before validation. The
  existing typed advisory event and 12-step hard stop are unchanged. This rule
  uses only typed runner outcomes, not task text, filenames, commands, verifier
  output, or arm identity, and applies identically to every strategy.
- **2026-08-31, campaign v17 closed after one `build-cython-ext` pair:** both
  arms passed 11/11 external tests. B0 used `runtime_complete` and passed the
  strict gate; FBGC completed the repository but did not emit the terminal
  call, then hit no-state-progress at step 51, so expansion stops. Its mechanism
  gate passed (four admissions, 47 pointer-bearing calls, four substitutive
  transitions, two archive reads). FBGC reduced total input 43.21%, official
  half-price input 34.60%, and peak input 53.70%, but increased fresh input
  174.84% and used 52 calls versus 39. These savings are not a win because the
  strict gate failed. Typed events show that the step-45 advisory correctly saw
  errors; after subsequent correction and successful validation, the one-shot
  advisory did not re-evaluate the clean second six-step window before the
  12-step hard stop. Any successor rule must be prospective and windowed.
- **2026-08-31, prospective v18 windowed recovery amendment:** Runtime tracks
  typed tool errors in independent six-step no-progress windows. A transition
  from an error-bearing window to a clean window emits a fresh completion
  advisory. If that transition lands exactly on the hard threshold, the model
  receives one additional turn to call `runtime_complete`; otherwise the next
  unfinished turn terminates normally. Successful state change resets all
  window state. A regression test covers six failing plus six clean validation
  turns, and the complete workspace validation is green. No fixture, prompt,
  Provider, verifier, arm-specific rule, or prior score is changed.
- **2026-08-31, campaign v18 closed after one `build-cython-ext` pair:** FBGC
  passed 2/11 external tests and B0 passed 11/11; neither called
  `runtime_complete`, so both strict gates fail and expansion stops. FBGC's
  mechanism gate passed with three admissions, 21 pointer-bearing calls, and
  two substitutive transitions. It reduced total input 55.60%, official
  half-price input 47.31%, peak input 32.16%, and Provider calls 29.73%, while
  increasing fresh input 117.15%. Quality mismatch invalidates those apparent
  savings as a win. FBGC's only advisory was already clean, so the new
  error-to-clean recovery grace was correctly inapplicable; B0 received two
  error-bearing advisories. V17 versus v18 also demonstrates substantial remote
  trajectory variance despite the same declared seed. No further v18 paid
  trials are run.
