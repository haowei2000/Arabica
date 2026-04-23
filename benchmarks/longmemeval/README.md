# LongMemEval

Wu et al., *LongMemEval: Benchmarking Chat Assistants on Long-Term
Interactive Memory*, ICLR 2025 (arXiv:2410.10813). Upstream repo:
`https://github.com/xiaowu0162/LongMemEval`.

## Why this benchmark

Five ability axes (single-session user / assistant / preference,
multi-session, temporal reasoning, knowledge update) with per-axis
accuracy breakdown. Matches the paper's claim that multi-level
disclosure surfaces distinct failure modes that a single accuracy
number hides.

## Files

- `dataset.py` — parser for the upstream JSON schema (see module
  docstring for field list). Tolerates extra fields.
- `scorer.py` — deterministic substring-match scorer mirroring the
  upstream `evaluation/match_eval.py`; an LLM-judge variant with the
  same signature can be dropped in later.
- `fixtures/sample.json` — four handwritten cases spanning four
  `question_type`s; used by the harness self-test in
  `tests/benchmarks/`.

## Running the full benchmark

1. Clone the upstream repo and follow their data-prep script. The
   release ships an S subset (~500 cases, short horizons) and an M
   subset (longer, gated). Copy the JSON you want to evaluate to
   `data/longmemeval_s.json` (or wherever).
2. Plug a Structure-backed agent into the runner:

   ```python
   from benchmarks.core import BenchmarkRunner
   from benchmarks.longmemeval import load_longmemeval, longmemeval_scorer
   # from benchmarks.adapters.structure import StructureAgent  # (planned)

   cases = load_longmemeval("data/longmemeval_s.json")
   report = asyncio.run(
       BenchmarkRunner(
           benchmark_name="longmemeval-s",
           agent=StructureAgent(workspace="lme-s"),
           scorer=longmemeval_scorer,
       ).run(cases)
   )
   ```

3. `report.per_ability_score` gives the headline breakdown; the cost
   ledger gives tokens-per-successful-case.

## TODO (tracked here, not in the codebase)

- [ ] `benchmarks/adapters/structure.py` implementing `AgentProtocol`
      against a Structure executor + event-sourced cost harvest.
- [ ] Optional LLM-judge scorer (`longmemeval_llm_scorer`) gated
      behind an env var so CI stays deterministic.
- [ ] Parallel runner with per-case checkpointing so 500+ case runs
      can resume after interruption.
