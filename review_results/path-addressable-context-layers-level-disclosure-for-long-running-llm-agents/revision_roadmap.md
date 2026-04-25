# Revision Roadmap

Status: **submission-blocking issues present**. Fix Priority 1 before any submission build.

## Priority 1 — gate-blocking (must fix before submission)

- [ ] **M01** — Run the experiments. Fill `tab:main` (B1–B4 + Structure × {accuracy, tokens, latency}) and `tab:ablation` (full + A1–A5) on the core 3-benchmark set. Replace every `\todo{--}` cell.
- [ ] **M02** — Strip every `\todo{...}` marker. Verify with `grep -nR "\\\\todo" paper/sections paper/main.tex` returns zero hits.
- [ ] **M03** — Reconcile §Limitations §Scope with §Experiments §Datasets. Either narrow the dataset list to {AgentBench, WebArena, LongBench-Agent} or rewrite the Limitations paragraph.
- [ ] **M04** — Specify backbone model + decoding parameters, step cap N, and per-run token cap B. Move the full table to §Appendix §Hyperparameters.
- [ ] **M05** — Either back the "sub-10 ms retrieval" claim with a §Scalability plot, or weaken the wording in the abstract and introduction.

## Priority 2 — major (must fix before camera-ready, strongly advised before submission)

- [ ] **M06** — Replace the three "and others" author lists in `references.bib` (`ye2025agentfold`, `xu2025agentdiet`, `zhang2025memact`) with full author rolls.
- [ ] **Mo01** — State the normalization assumed for `s_sim` (raw cosine? min-max?) and either rescale `\bar r` into the same range or add an explicit normalisation step in the rating-blend formula.
- [ ] **Mo02** — Reframe ablation A5 ("no audit replay") as a reproducibility-only artifact, or describe a mechanism by which it should hurt task accuracy. Drop the audit log from RQ3 if the former.
- [ ] **Mo05** — Compress the five-bullet contribution list to 2–3 research contributions; move "implement" + "evaluate" + "release" into a single Reproducibility/Artifacts statement.
- [ ] **Mo07** — Rename the token budget symbol `B` (suggest `T_{\text{budget}}` or `\mathcal{B}_{\text{tok}}`) to avoid clashing with baseline labels B1–B4.

## Priority 3 — moderate (camera-ready window)

- [ ] **Mo03** — Specify glance update semantics in §Method §Multi-Level Disclosure: precomputed glance is invalidated on `update()` / recomputed lazily / treated as snapshot. Listing 2 should reflect the chosen semantics.
- [ ] **Mo04** — Spell out the replay invariants (idempotent rating updates, deterministic event ordering, seed state) or weaken "reconstructs the exact $\bar r$" to an approximation claim.
- [ ] **Mo06** — Replace the inline "pin OR steps AND seconds" prose with a parenthesized display equation: `keep(e) := pin(\tau) ∨ (Δt_steps ≤ \Pi(\tau).steps ∧ Δt_secs ≤ \Pi(\tau).seconds)`.

## Priority 4 — polish

- [ ] **Mi01** — Anchor the 50 B / 1 KB disclosure budgets to a window-budget calculation or move to a parameters table.
- [ ] **Mi02** — Add a one-line justification for `α = 0.7` (e.g., "tuned on the AgentBench dev split").
- [ ] **Mi03** — Tighten the "roughly one plan–act cycle" prose to match Table 4's actual TTL range.
- [ ] **Mi04** — Convert Listings 1–2 from `figure`+`verbatim` to `lstlisting` or `algorithmic`, with a proper Listing counter.
- [ ] **Mi05** — Either drop {BFCL, AgentBoard, LOFT, GAIA, WorkArena} from §Experiments or commit to an "Extended Battery" appendix subsection.

## Suggested order of work

1. Decide the experimental scope (Priority 1: M03 first — defines what M01 means).
2. Pin the implementation knobs (M04) so experiments are reproducible from configuration.
3. Run experiments → fill Tables 2–3 (M01).
4. Strip TODOs and rewrite abstract + conclusion headlines around the actual numbers (M02 + intro/conclusion).
5. Run the scalability sweep before deciding whether to keep the "sub-10 ms" claim (M05).
6. Sweep the bibliography (M06) and the formula/notation cleanup (Mo01, Mo07, Mo06).
7. Camera-ready polish (Priority 4).
