# Benchmark Protocol (Pre-Registered)

**Status:** FROZEN as of 2026-07-10. Any change after the first live run
must be recorded in the Amendments section with a date and a reason.
Results produced under a modified protocol must say so in the paper.

This document pre-registers the experimental design for the paper's
research questions (RQ1–RQ4 in `paper/sections/experiments.tex`) so
that arm selection, case sampling, repetition counts, statistics, and
the accept/reject decision rule are fixed *before* any live LLM run.

---

## 1. Benchmarks and datasets

| Benchmark | Split | Source | Cases used |
|---|---|---|---|
| LongMemEval | `longmemeval_s` | upstream release JSON | frozen list, ≥200 |
| LoCoMo | full QA set | upstream release JSON | frozen list, ≥200 |
| LongMemEval-V2 | Insert/Query split | upstream release JSON | frozen list, ≥200 (or full split if smaller) |

Rules:

1. Raw datasets live under `data/` (gitignored). They are **never**
   committed.
2. Before the first live run, record the SHA-256 of every dataset file
   in `benchmarks/protocol/checksums.txt` (committed). Every runner
   invocation verifies the checksum and refuses to run on mismatch.
3. Case selection uses the harness's hash-based sampling with
   `sample_seed = 20260710`. The resulting case-ID lists are written to
   `benchmarks/protocol/case_lists/<benchmark>.txt` and committed.
   Live runs execute exactly these lists — no re-sampling.

## 2. Arms

All arms share the same reader model, `temperature = 0`, `top_k = 6`,
and a 120,000-character materialised-context cap. Only context
selection differs. Entry point:
`benchmarks/scripts/run_full_memory_benchmark.py`.

| Arm | Name | Description |
|---|---|---|
| B1 | `FullText` | All available chunks, subject to the cap |
| B2 | `NaiveRAG` | Top-k similarity retrieval over the same chunks |
| B3 | `StructurePathMemory` | Path-addressable workspace adapter with disclosure levels |
| C0 | `ClosedBook` | **Contamination control:** reader answers with no context at all |

C0 interpretation: if any arm's accuracy is not meaningfully above C0,
the benchmark answers are attributable to the reader model's parametric
knowledge and the comparison on that benchmark is void.

### Mechanism-activation precondition for B3

A B3 run is valid **only if** the run log confirms the context
machinery actually fired: multi-run ingestion populated the workspace,
batch `LOAD_KEY` compression occurred, and the GC policy was applied.
The runner must record, per case, which mechanisms were active
(`load_key_batches > 0`, `gc_applied`, disclosure-level counts).
Runs where every case executed as a single fresh run with no batch
compression measure a retrieval variant, not the contribution
(root-cause finding, 2026-07-04 review), and must not be reported as B3.

## 3. Repetition and variance

- Each (benchmark × arm) cell is run **k = 5** times.
- Run seeds: `20260710 + i` for `i ∈ {0..4}` (applied to any stochastic
  component; the scorer and sampling stay deterministic).
- Report mean ± sample standard deviation for accuracy and token cost.
- All 5 runs are reported. Discarding a run requires an amendment entry
  with the failure reason (e.g. provider outage) and the raw artifact
  retained.

## 4. Statistics

- Primary comparison: **paired bootstrap** over per-case scores
  (B3 vs B1, B3 vs B2), 10,000 resamples, on the pooled per-case means
  across the 5 repetitions. Report 95% CIs and the two-sided p-value.
- Efficiency metric: **tokens per scored point** = mean total prompt +
  completion tokens per case ÷ mean per-case score. Bootstrap CI
  computed the same way.
- "Comparable accuracy" means: B3's accuracy CI overlaps or exceeds the
  baseline's (no significant loss at p < 0.05).

## 5. Decision rule (pre-registered)

The paper's headline claim (RQ1) is **supported** iff, on at least
**2 of the 3** benchmarks:

1. B3 achieves comparable accuracy to the better of B1/B2 (per §4), and
2. B3 significantly improves tokens per scored point over that same
   baseline (bootstrap 95% CI excluding zero), and
3. All arms clear the C0 contamination floor.

Otherwise the claim is reported as unsupported and the paper's framing
falls back to the systems/reproducibility contribution. No
post-hoc benchmark swaps: the three benchmarks named in §1 are the
denominator.

## 6. Harness integrity gates (must pass before any live run)

1. `uv run pytest tests/benchmarks -m unit` fully green.
2. **Oracle gate:** `EchoAgent` fed the reference scores exactly 1.0 on
   every bundled fixture.
3. **Anti-oracle gate:** a scrambled/wrong-answer agent scores ≈ 0 on
   the same fixtures (guards against degenerate scorers that reward
   anything).
4. Fixture discriminativeness: for each benchmark, at least one fixture
   case per ability tag where B1 and an empty-context arm score
   differently.
5. Dataset checksums recorded (§1.2).

## 7. Ablations (run only if RQ1 headline supported)

Same protocol (§1–§4) with these arms, pre-registered:

- **A1 disclosure:** single-level (forced `detail`) vs full three-level.
- **A2 rating:** read-path on (α = 0.7 blend + threshold) vs off;
  plus oracle-rating and placebo-rating (random ratings) arms to bound
  the mechanism's ceiling and floor.
- **A3 GC:** default policy vs all-pinned (flat history) vs
  aggressive policy vs no-floor; plus a replay-determinism check that
  re-materialising from the log reproduces byte-identical prompts.

## 8. Reporting

Every run's JSON artifact must include: git commit hash, model name,
provider base URL, protocol version (this file's git blob hash), seeds,
case-list hash, per-case JSONL, prompt/completion tokens, latency, and
mechanism-activation counters (§2). Paper tables are generated from
these artifacts only — no hand-entered numbers.

## Amendments

*(none yet)*
