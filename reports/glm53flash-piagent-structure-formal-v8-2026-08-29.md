# GLM-5.3-Flash Pi Agent vs Structure Formal Campaign v8

Date: 2026-08-29

## Decision

Structure is the formal winner in this frozen 20-pair native agent-stack
campaign.

- Strict resolution: Pi Agent 18/20; Structure 20/20.
- Comparable double-success pairs: 18.
- Economic-token wins on comparable pairs: Structure 17; Pi Agent 1; ties 0.
- Geometric mean `Pi / Structure` economic-token ratio: **3.1691x**.
- Paired 10,000-resample bootstrap 95% CI: **[2.4613x, 4.0178x]**.
- Mean `Pi / Structure` duration ratio: **3.9917x**.
- Harness decision: `overall_winner = structure`.

Economic tokens are fresh input plus output. Cached input is excluded, and
reasoning tokens are not added a second time because they are a subset of
output.

## Frozen treatment

- Four unchanged scenarios from `benchmarks/cli-comparison/suite.json`.
- Five repeats per scenario and surface, 40 trials total.
- Deterministically interleaved order from seed `20260829`.
- Model: `glm/glm-5.3-flash`; requested thinking: `high`.
- Timeout: 900 seconds per trial.
- Pi host version: `0.84.1`.
- Pi executable SHA-256:
  `840d1e8e689ed9e4937bcb00b9a810e02a8567d9afb10a47097f11ca93ea1521`.
- Official Pi Agent source revision:
  `4aa91efc636515a3d80c9b09105bf0b5d3d5c3bf` (clean checkout).
- Release harness SHA-256:
  `961d7d8add33ee1cc696a256eec37cfda54df8748a956e81ea7eddd8d3340457`.
- Structure dirty-tree digest at preflight:
  `0860d8d2b4685fc36234115abcbd96fa06f1ab3aa6315ed0e9784d9f25a859f8`.

Each agent retained its native prompt, tools, loop, and context implementation.
The fixture, task prompt, model request, timeout, allowed changes, and hidden
verifier were shared. No code or experiment input changed after manifest
creation.

## Aggregate measurements

| Metric | Pi Agent | Structure |
|---|---:|---:|
| Resolved | 18/20 | 20/20 |
| Fresh input | 170,759 | 60,117 |
| Output | 103,177 | 26,622 |
| Economic fresh tokens | 273,936 | 86,739 |
| Total input | 170,759 | 89,109 |
| Cached input | 889,152 | 28,992 |
| Reasoning output | 61,137 | 17,191 |
| Tool calls | 140 | 69 |
| Failed tool results | 58 | 1 |
| Total duration | 2,303.766 s | 628.377 s |
| Timeouts | 0 | 0 |
| Scope failures | 2 | 0 |

The primary cost estimate is success-conditioned and paired. Aggregate totals
retain failed attempts for audit and operational-cost visibility.

## Per-scenario observations

- `falsey-config`: Pi [9,452, 7,971, 9,311, 8,561, 8,892]; Structure
  [2,868, 4,327, 3,363, 2,119, 1,858]. All ten trials passed.
- `bounded-retry`: Pi [24,814, 12,834, 26,873, 22,236, 18,669]; Structure
  [3,117, 3,112, 3,867, 3,899, 4,036]. Pi repeats 3 and 4 failed the
  scope gate after modifying `package.json` and `test/retry.test.js`; Structure
  passed all five.
- `stable-dedupe`: Pi [6,605, 7,889, 9,847, 11,455, 9,036]; Structure
  [3,322, 1,728, 3,956, 1,719, 3,662]. All ten trials passed.
- `config-batch`: Pi [12,226, 16,513, 13,189, 23,907, 13,656]; Structure
  [4,896, 7,530, 17,180, 6,392, 3,788]. Structure had a real long-tail
  regression on repeat 3, where Pi won 13,189 to 17,180 tokens. It is retained
  in the paired estimate.

## Audit and limitations

Every trial report records raw stdout and stderr paths plus SHA-256 digests. A
post-run audit recomputed all 80 hashes with zero mismatches. Pi's official
bootstrap state under `.pi/piagent-state` is treatment-owned runtime state and
was excluded from task scope by the frozen harness; all other out-of-scope
project mutations failed the trial.

This campaign is formal evidence for the pinned GLM model, Pi revision, adapter,
and small JavaScript suite. It is not a universal ranking over other models,
languages, repository sizes, or later Pi versions. The short tasks did not
activate FBGC, so this is a full agent-stack comparison rather than evidence
that FBGC itself caused the result.

## Reproduction artifacts

- Manifest: `.local/benchmarks/formal-glm53flash-20260829-v8/piagent-structure/manifest.json`
- Preflight: `.local/benchmarks/formal-glm53flash-20260829-v8/piagent-structure/preflight.json`
- Adapter hashes: `.local/benchmarks/formal-glm53flash-20260829-v8/piagent-structure/adapter.sha256`
- Trial reports: `.local/benchmarks/formal-glm53flash-20260829-v8/piagent-structure/trials/`
- Raw process outputs: `.local/benchmarks/formal-glm53flash-20260829-v8/piagent-structure/workspaces/*/process-output/`
- Summary: `.local/benchmarks/formal-glm53flash-20260829-v8/piagent-structure/summary.json`
