# GLM-5.3-Flash Pi Agent vs Structure Paired Campaign

Date: 2026-08-29

> Superseded by the complete five-repeat campaign in
> `reports/glm53flash-piagent-structure-formal-v8-2026-08-29.md`. This earlier
> partial campaign is retained as historical evidence and is not pooled into v8.

## Result

Structure wins this pre-registered small JavaScript agent-stack campaign.

- Completed pairs: 7
- Strictly resolved: Structure 7/7; Pi Agent 5/7
- Comparable double-success pairs: 5
- Fresh-token wins on comparable pairs: Structure 5; Pi Agent 0; ties 0
- Geometric mean `Pi / Structure` economic-token ratio: **3.4653x**
- Paired bootstrap 95% CI: **[2.0583x, 5.8015x]**
- Mean `Pi / Structure` duration ratio: **4.1483x**
- Harness decision: `overall_winner = structure`

Economic tokens are `fresh input + output`. Cached input is not counted. Reasoning
tokens are already a subset of output tokens and are not added a second time.

## Fairness and execution contract

The campaign used the existing agent-stack suite without changing fixtures or
prompts for either agent. Each surface used its native prompt and tool stack; only
the shared provider transport was adapted from OpenAI Responses to the GLM Chat
Completions endpoint. The same requested model and thinking level were used:

- Model: `glm/glm-5.3-flash`
- Thinking: `high`
- Repetitions: 2
- Trial timeout: 900 seconds
- Manifest seed: `20260829`
- Suite: `benchmarks/cli-comparison/suite.json`
- Pi host version: `0.84.1`
- Pi executable SHA-256: `840d1e8e689ed9e4937bcb00b9a810e02a8567d9afb10a47097f11ca93ea1521`
- Pi Agent source revision: `4aa91efc636515a3d80c9b09105bf0b5d3d5c3bf` (clean)
- Structure source revision at preflight: `81ea832b5f2acccd7e8e182660115a39377b7550`
- Structure dirty-tree digest at preflight: `66ffd5a9696f5ab89b92e9c93f1f255f8d304ea9571f9862bab0dcf0a607d162`
- Harness executable SHA-256: `eeb0fd41dc589654a2880c5279d57a4fb417daa03ba7053df56089203766c300`

Preflight reported a valid manifest, matching suite digest, pinned Pi source, clean
Pi checkout, available executables, and no blockers.

## Paired results

| Scenario / repeat | Pi tokens | Structure tokens | Pi / Structure | Strict result |
|---|---:|---:|---:|---|
| falsey-config / 1 | 11,232 | 1,349 | 8.326x | both pass |
| bounded-retry / 1 | 18,501 | 3,161 | ineligible | Pi scope failure |
| stable-dedupe / 1 | 7,614 | 1,921 | 3.964x | both pass |
| config-batch / 1 | 13,081 | 4,586 | 2.852x | both pass |
| falsey-config / 2 | 8,275 | 2,073 | 3.992x | both pass |
| bounded-retry / 2 | 15,401 | 6,532 | ineligible | Pi scope failure |
| stable-dedupe / 2 | 7,338 | 5,518 | 1.330x | both pass |

Both Pi `bounded-retry` runs passed the functional verifier but modified
`package.json` and `test/retry.test.js`, which were outside the allowed task scope.
They are therefore strict failures and are excluded from cost comparison. Structure
passed the same functional and scope gates in both repeats. This exclusion rule was
part of the harness and was not introduced after seeing the result.

## Interpretation and claim boundary

This is formal evidence for this pinned GLM-5.3-Flash, high-thinking, seven-pair
small JavaScript suite. It supports the claim that Structure was more reliable and
used fewer economic tokens than this pinned Pi Agent configuration on every eligible
pair. The confidence interval remains entirely above 1.0 in the `Pi / Structure`
direction.

It is not a universal claim over all repositories, models, task families, or Pi
Agent versions. In particular, these short tasks do not activate FBGC, so this
campaign evaluates the complete agent stacks rather than attributing the result to
Structure's context policy.

## Reproduction artifacts

- Manifest: `.local/benchmarks/formal-glm53flash-20260829-v7/piagent-structure/manifest.json`
- Preflight: `.local/benchmarks/formal-glm53flash-20260829-v7/piagent-structure/preflight.json`
- Trial reports: `.local/benchmarks/formal-glm53flash-20260829-v7/piagent-structure/trials/`
- Summary: `.local/benchmarks/formal-glm53flash-20260829-v7/piagent-structure/summary.json`
