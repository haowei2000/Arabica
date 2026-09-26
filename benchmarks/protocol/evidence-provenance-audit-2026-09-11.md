# Evidence Provenance Audit — 2026-09-11

Purpose: establish which retained results can be cited, re-verified, or re-run
before any paper text is written from them. No Provider calls were made.

Each result was checked for four failure modes found during this audit:

1. **Code identity** — is the executed code recorded (clean revision, or a
   dirty-tree digest)?
2. **Binary survival** — does the executed binary still exist with a matching
   hash?
3. **Contrast validity** — do the compared arms actually differ in the property
   being claimed?
4. **Forced admission** — was FBGC admission scheduled or forced rather than
   chosen by the production policy?

## Summary

| Result | Code identity | Binary | Verifiable | Re-runnable | Citable as |
|---|---|---|---|---|---|
| v5-01 Phase 0 | clean `a12f72a` + binary hash | archived, matches | yes | yes | negative qualification result |
| v8 agent-stack (Codex, Pi) | tree digests + harness hash | **lost** (overwritten 2026-09-01) | yes, via trial hashes | no | agent-stack comparison, with disclosure |
| Phase 1 deterministic ablation | base `49cb4c3`, dirty, campaign tree digest | archived, **never hashed** | partly | rerun at HEAD | substitution mechanics only |
| tier-b sweeps v5-v13 (incl. v7) | base `81ea832`, dirty, **no digest** | lost, never hashed | **no** | rerun at HEAD | nothing until rerun |
| Memory-benchmark pilots (2026-05-21) | uncommitted tree | n/a | n/a | n/a | **not evidence for the paper's claim** |

## Findings

### v5-01 Phase 0 — fully reproducible

Clean revision `a12f72a`; `harbor_agent` sha256 `938247cb...` archived.
Qualification failed all four gates; see that campaign's `final-report.md`.

### v8 agent-stack — verifiable, not re-runnable

- Structure ran in-process inside the harness (`CoreRuntime` and
  `SessionManager`, `benchmarks/runtime-short-memory/src/cli_comparison.rs:1333-1345`);
  only Codex and Pi were spawned as subprocesses.
- At runtime the harness reads only suite-digested inputs (suite, scenario
  prompts, fixtures) plus its own outputs. Both comparisons used harness
  sha256 `961d7d8a...` and suite digest `6060d089...`, so **the Structure code
  was byte-identical in both comparisons**; the differing dirty-tree digests
  reflect files outside the compiled runtime.
- Dirty trees were recorded by digest only; no patch was archived. The harness
  binary was overwritten on 2026-09-01, and no copy exists in any worktree.
- All 80 trial output hashes were re-audited at the time with zero mismatches.
- Required disclosure: the harness binary was not retained, so results are
  verifiable against retained trial hashes but cannot be re-executed. Existing
  scope limits also apply: four-scenario JavaScript suite, one model, and FBGC
  never activated.

### Phase 1 deterministic ablation — partly verifiable

- Campaign v1 freeze: base `49cb4c3`, dirty digest `b9a89c4e...`.
- Produced by the package main binary `structure-short-memory-benchmark`
  (`scaling.rs`), which the campaign **did not freeze**. The 2026-09-01 05:40
  release build is archived; it was built in the same session as the frozen,
  matching `cli_comparison` and is very likely the producer, but this cannot be
  proven.
- Checkpoints were scheduled by the harness (`prior_admissions: 0`,
  `calls_since_last_admission: 0`, checkpoint sizes 2/4/8). This is evidence
  that substitution works when it happens, not that the runtime chooses to
  admit; Phase 0 shows it does not at `cached_input_cost_bps = 0`.

### tier-b deterministic sweeps — not verifiable

- All 19 retained artifacts (v5, v6, v7, v11 admission, v11 epoch-cap, v13):
  `git_revision: 81ea832`, `git_worktree_dirty: true`, no digest, no binary
  hash.
- 2,331 lines of uncommitted runtime and benchmark changes separate `81ea832`
  from the next commit `7b90677`; the sweeps ran on unrecorded intermediate
  states.
- `MechanismQualification` is absent at `81ea832` and was first committed in
  `659f082` (2026-09-06), so forced admission is implausible, but the tree is
  unrecorded.
- v7's `pointer_gc_checkpoint_batches: 4` came from uncommitted epoch-cap code.
- The projection-byte figures (4.6% / 13.7% / 18.6%) are therefore not
  citable as-is.
- Recoverable at zero cost: the fixture is deterministic and LLM-free. v7's
  recorded configuration maps to `tier_b` flags `--fixture`,
  `--single-message-tools {12,24,32}`, `--strategies` (B0 and FBGC),
  `--repetitions 5`, `--suite-id tier-b-write-file`. Confirm the `--strategies`
  value syntax with `tier_b --help`. A rerun at HEAD tests the committed policy
  and is a clean replacement, not a byte-exact reproduction of v7.

### Memory-benchmark pilots — invalid as evidence for the paper's claim

- `StructureMemory` selects context with the same function, chunk records, and
  `top_k` as `NaiveRAG` (`benchmarks/baselines/llm_agent.py:86`,
  `benchmarks/adapters/structure_memory.py:313`); with no recorded ratings the
  rating read-path is a pass-through.
- On all 7 discordant cases of the favourable pilot, both arms selected
  identical context; prompts differed by 30-45 tokens of formatting.
- Pooled and deduplicated across both 2026-05-21 runs: LongMemEval 96 cases,
  mean difference +0.031, 95% bootstrap CI [-0.031, +0.104], p = 0.366;
  LoCoMo 99 cases, p = 1.000.
- Both pilots ran on an uncommitted tree between `cf87f9b` and `d4003b0`.
- The FullText baseline keeps the most recent ~24% of each LongMemEval-S
  haystack under the 120,000-char cap (`llm_agent.py:96` keeps the tail).
- The paper's disclosure mechanism exists in code (`Context.disclose` in
  `src/structure/models/context/context.py`; Rust
  `DisclosureLevel { Glance, Overview, Detail }` and the
  `context.set_disclosure` operation in `crates/arabica-protocol`), but no
  benchmark adapter calls it.

## Actions taken

- Surviving binaries copied, with checksums, to
  `.local/benchmarks/archived-binaries/`; no campaign directory was modified.
- `longmemeval-confirmatory-freeze-2026-09-11.md` withdrawn;
  `phase-2-power-analysis-2026-09-11.md` memory-track section corrected. Both
  retain their original text.
- Root cause — freezes record binary hashes but leave the files in `target/` —
  flagged as a separate task.

## What can be cited today

1. The v5-01 Phase 0 negative result.
2. The v8 agent-stack ratios, with the binary-retention disclosure and existing
   scope limits.
3. Phase 1 as evidence of substitution mechanics with partial provenance —
   preferably after a clean rerun at HEAD.

Not citable until rerun: any tier-b projection-byte figure. Not citable at all:
the memory-benchmark pilots as evidence for path-addressable disclosure.
