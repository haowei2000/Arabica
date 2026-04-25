# Deep Review Report

**Paper**: `/Users/haowei/Projects/Structure/paper/main.tex`
**Title**: Path-Addressable Context Layers: Multi-Level Disclosure for Long-Running LLM Agents
**Venue target**: EMNLP / ACL (anonymous review style)
**Reviewed**: 2026-04-25
**Mode**: deep-review · focus=full · language=EN

## Overall Assessment

This is a methods-heavy submission that has **not yet had its experiments run**. The Method section is detailed and well-positioned against the 2025 cluster of competing work (Context-Folding, AgentFold, ACON, AgentDiet, Memory-as-Action), and the conceptual contribution — promoting context management to a path-addressable, multi-level-disclosure substrate orthogonal to time-axis trajectory compression — is clearly articulated. However, the paper is currently a **scaffold with content**: Section 6 (Results) is composed entirely of `\todo{--}` cells across two tables and four analysis paragraphs, the abstract still carries a visible `\todo{Quantitative headline}` marker, the conclusion has the matching placeholder, and three central reproducibility knobs (backbone model, step cap N, token budget B) remain unfilled. The paper also has a self-consistency contradiction between its Limitations scope (3 benchmarks) and its Experiments dataset list (13+).

**Issue counts**: 6 major, 7 moderate, 5 minor.
**Reviewer recommendation**: **Major Revision** (or, more honestly, *withhold submission until experiments are completed*).
**Editor verdict**: **Desk-reject in current form** — visible TODO markers in the abstract and empty results tables are sufficient triggers in any peer-reviewed track.

## Decision Signals

| Signal | Verdict |
|---|---|
| EIC desk-reject screen | Desk-reject (placeholder abstract + empty results) |
| Methodology rigor (Method sections) | Strong; well-formalized |
| Literature positioning | Strong; concrete delta against 2025 cluster |
| Self-consistency | Weak (Limitations vs. Experiments scope clash) |
| Empirical evidence | None present |
| Reproducibility | Weak (model and budgets unspecified) |

## Top Three Revision Priorities

1. **Run the experiments.** Fill at minimum Tables 2 and 3 (main + ablation) on the core 3-benchmark set before any further structural changes. Without numbers, the abstract / introduction / conclusion remain unverifiable. (Resolves M01, M02, indirectly M05.)
2. **Reconcile Limitations §Scope of evaluation with Experiments §Datasets.** Either narrow the experiments to AgentBench/WebArena/LongBench-Agent and drop SWE-bench/AppWorld/OSWorld/etc., or rewrite the Limitations paragraph to acknowledge code, GUI, and tool-use coverage. (Resolves M03, partially Mi05.)
3. **Pin the implementation knobs.** State the backbone model + decoding parameters, the step cap, and the token budget in §Experiments §Implementation Details, and copy them into the Hyperparameters table in the Appendix. (Resolves M04, partially Mi02.)

---

## Major Issues (6)

### M01 — Results section is entirely placeholder
- **Quote**: `Flat window (B1) & \todo{--} & \todo{--} & \todo{--}` (Table 2) and matching `\todo{--}` cells across Table 3 (ablation). 28 `\todo` markers remain across the manuscript, concentrated in §Results, §Experiments, and §Appendix.
- **Why it matters**: every empirical claim in the abstract, intro, and method ("matches flat-window accuracy at a fraction of the token cost", "sub-10 ms retrieval", "$40$–$60\\%$ input-token reduction-style framing of comparable claims") relies on Section 6 to discharge it.
- **Action**: complete at least the core 3-benchmark main-result row plus A2 (single level) and A4 (no GC) — those two ablations alone substantiate the disclosure and GC stories that the rest of the paper builds on.

### M02 — Visible `\todo{Quantitative headline}` marker in the abstract
- **Quote**: `\todo{Quantitative headline: report token reduction, success-rate gain, and latency on the target benchmarks.}` (sections/abstract.tex:28)
- **Why it matters**: the `\todo` macro renders red bracketed text in the PDF. ACL/EMNLP desk reviewers will see this immediately; it is sufficient for a desk-reject decision before reviewer assignment. Conclusion (sections/conclusion.tex:7) carries the matching placeholder.
- **Action**: strip the macro before any submission build; confirm via `grep -nR "\\\\todo" paper/` that zero markers remain.

### M03 — Limitations §Scope contradicts Experiments §Datasets
- **Quote**: "Our benchmarks (AgentBench, WebArena, LongBench-Agent) cover English tool-use, web interaction, and long-document question answering. We do not evaluate code-generation agents, multi-lingual agents, or embodied agents." (sections/limitations.tex:3–6)
- **Conflict**: §Datasets lists SWE-bench (code-generation), AppWorld (interactive coding), OSWorld (multimodal desktop), τ-bench, GAIA, WorkArena, BFCL-v4, AgentBoard, RULER, HELMET, LOFT, LoCoMo, LongMemEval — the manuscript both claims to cover code agents and disclaims them.
- **Action**: pick one truth. Either compress §Datasets to match the Limitations scope, or rewrite the Limitations paragraph to acknowledge the broader coverage.

### M04 — Backbone model, step cap N, and token budget B are unspecified
- **Quote**: "We evaluate with a frontier chat model as the agent backbone. `\todo{Specify exact model, e.g., GPT-4.1 or Claude Sonnet, plus temperature, top-$p$, and context window.}` ... We cap each trajectory at `\todo{$N$}` steps and a token budget of `\todo{$B$}` per run".
- **Why it matters**: a reviewer cannot judge whether the comparison against B1–B4 is fair without knowing the underlying LLM and the budgets. The Limitations paragraph "All results are conditioned on the specific backbone model used" amplifies the gap — the result is a paper that simultaneously caveats its model dependency and refuses to name the model.
- **Action**: pin the model name, decoding parameters, step cap (typical: 30–50 for AgentBench, 100+ for SWE-bench), and per-run token cap.

### M05 — Sub-10 ms retrieval claim asserted without measurement
- **Quote**: "the system sustains sub-10\,ms retrieval over hundreds of entries via in-memory caches and HNSW indices" (intro), and "reduces warm path lookups from roughly 500\,ms to under 10\,ms" (method).
- **Why it matters**: both numbers are presented as system properties, not as measurements drawn from a benchmark. §Results §Scalability is a `\todo{Include a log-log plot...}`. A reviewer who tries to ground the claim against any of RULER/HELMET/LongMemEval will find no plot, no histogram, no per-query latency table.
- **Action**: produce one scalability figure (10² → 10⁵ entries) **before** retaining the claim in the abstract; otherwise downgrade the wording to "an in-memory cache materially reduces warm-path latency in our deployment".

### M06 — Three bibliography entries use 'and others' placeholder authors
- **Entries**: `ye2025agentfold` ("Ye, Rui and others"), `xu2025agentdiet` ("Xu, Siyuan and others"), `zhang2025memact` ("Zhang, Yuxiang and others").
- **Why it matters**: these are exactly the works the paper positions itself most directly against (concurrent 2025 cluster). Submitting with abbreviated author metadata risks a bibliographic-quality EIC flag and misattributes credit on the closest competitors.
- **Action**: pull the full author lists from arXiv. The bib comment header at line 1 already flags this — the work is half-done.

---

## Moderate Issues (7)

- **Mo01 — Rating-blend scale mismatch**: $s = \alpha \cdot s_\text{sim} + (1-\alpha) \cdot \bar r$ blends a similarity (cosine, in [-1,1] or [0,1] depending on normalization) with a rating mean in [-1,1] without a stated rescaling step. State the normalization or add a normalisation step before the convex combination.
- **Mo02 — RQ3/A5 conflates substrate with mechanism**: A5 ("No audit replay") tests reproducibility, not a path through which the agent gains accuracy. Either reframe A5 as a reproducibility-only artifact and drop it from RQ3, or describe an explicit agent-level mechanism by which the audit log lifts task accuracy.
- **Mo03 — Glance staleness**: Listing 2 falls back to `self.content[:50]` when `self.glance` is null. The "always consistent with stored state" claim should specify whether precomputed glances are recomputed on update, invalidated, or held as snapshots.
- **Mo04 — Replay determinism vs. denormalised aggregate**: the "replaying events up to step $t$ reconstructs the exact $\bar r$" claim depends on idempotent and order-deterministic application of `context.rated` events to the denormalised pair. State the invariants or weaken "exact" to "approximate (subject to floating-point summation order)".
- **Mo05 — Five-contribution list mixes primitives, mechanisms, implementation, and release artifact**: ACL/EMNLP reviewers down-weight contribution lists that count engineering deliverables. Compress to 2–3 actual research contributions.
- **Mo06 — GC predicate precedence not parenthesized**: the predicate "pin OR steps AND seconds" is intended as `pin ∨ (steps ∧ seconds)`. Convert to a display equation with explicit grouping.
- **Mo07 — Symbol B is overloaded**: `B` is used both as the token-budget scalar (Method §Problem Formulation) and as the baseline-name prefix `B1..B4`. Rename one.

---

## Minor Issues (5)

- **Mi01** — The 50 B / 1 KB disclosure budgets appear as defaults; tie them to a window-budget calculation ("200 glances × 50 B = 10 KB ≈ 6.4% of a 16k-token window") or move to a Hyperparameters table.
- **Mi02** — Default $\alpha=0.7$ presented without justification; pending the rating-sweep TODO.
- **Mi03** — "Step-TTL of roughly the width of a single plan–act cycle" is an oversimplification of Table 4 (range 2–10).
- **Mi04** — Listings 1–2 typeset as `figure`+`verbatim` floats with `Listing` captions; reviewers expect `lstlisting` or `algorithmic`.
- **Mi05** — "Extended battery in the appendix" is promised but the appendix carries no extended-battery table.

---

## What the paper does well

- **Method exposition**: §3 is unusually clear for a systems-flavoured NLP paper. The three-layer split (addressing / disclosure / audit) is principled and the orthogonality argument is well-stated.
- **Literature positioning**: §2 distinguishes the paper from MemGPT, A-MEM, Context-Folding, AgentFold, MemAct, AgentDiet, ACON, and LATTICE on a clear axis (address vs. time, declarative vs. learned). This is a serious related-work section.
- **Honest Limitations**: the rating-collapse and hand-tuned-TTL paragraphs are exactly what a reviewer would otherwise demand. They survive scrutiny — except for the §Scope contradiction flagged in M03.
- **Reproducibility framing**: the event-sourced audit log + released configurations is a credible reproducibility story, conditional on M04 being fixed.

---

## Committee Roll-Up

| Reviewer role | Verdict |
|---|---|
| Editor (desk-reject screen) | **Desk-reject in current form** (M01, M02 visible in abstract). |
| Theory contribution | Pass. Path-addressable multi-level disclosure is a coherent primitive; rating + GC are well-defined mechanisms. Compress contribution list (Mo05). |
| Literature dialogue | Pass. Concrete delta against 2025 cluster. Fix author metadata (M06). |
| Methodology transparency | Major revision. Backbone, budgets, normalisation, replay invariants all need to be pinned (M04, Mo01, Mo04). |
| Logic / argument coherence | Major revision. Contradiction between Limitations and Experiments scope (M03); RQ3 conflates substrate with mechanism (Mo02). |

Composite committee score (formula in skill spec, capped at 4.0 if Editor desk-rejects):
`9.0 − 1.5·6 − 0.7·7 − 0.2·5 = 9.0 − 9.0 − 4.9 − 1.0 = −5.9` → floored to 1.0; capped by Editor desk-reject ceiling at 4.0.
**Final committee score: 4.0/10** (current state). After fixing the six major issues, the paper plausibly lands at 6.5–7.5.

---

## Artifacts in this workspace

- `final_issues.json` — structured issue bundle (this report's source of truth)
- `revision_roadmap.md` — prioritized revision checklist
- `peer_review_report.md` — Summary / Major / Minor / Recommendation prose form (auto-generated, thin)
- `phase0_context.md` — script-level findings from `audit.py --mode deep-review`
- `paper_summary.md` — section-by-section paper summary
