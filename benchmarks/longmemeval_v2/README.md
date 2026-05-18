# LongMemEval-V2

LongMemEval-V2 evaluates long-term memory for LLM agents over large
environment-experience histories. Unlike the original LongMemEval adapter,
which consumes prepared conversation sessions and scores answer strings, this
adapter keeps the memory-system shape explicit:

1. insert history trajectories into a memory system
2. query the memory with a later question
3. return an answer plus supporting evidence ids

The checked-in fixture is synthetic and intentionally small. It validates
loader, scorer, and runner wiring in CI without downloading the full upstream
dataset or storing multimodal trajectories in the repository.

## Local Smoke Run

```bash
pytest tests/benchmarks/test_longmemeval_v2.py -m unit
```

The oracle `EchoAgent` should score `overall_score == 1.0` over the fixture.

## Full Dataset Integration Notes

The public release is loaded from:

- `questions.jsonl` for question, answer, and `eval_function`
- `haystacks/lme_v2_{small,medium}.json` for per-question trajectory ids
- `trajectories.jsonl` for lazy byte-offset trajectory reads

The scorer now recognises the release evaluator strings observed locally:

- `norm_phrase_set_match`
- `norm_phrase_set_match_ordered`
- `mc_choice_match`
- `mc_choice_set_match`
- `llm_abstention_checker`
- `llm_gotchas_checker`

The first four are deterministic. The two `llm_*` evaluator names are routed
through the official function selector but fall back to normalized answer
containment until a separate judge model is wired.

Trajectory rendering assigns stable state citations such as
`<trajectory_id>:s<state_index>`, includes screenshot paths when present, and
keeps the most question-relevant states per trajectory in the reader context.

Sampling is configured in the full runner:

```bash
uv run python -m benchmarks.scripts.run_full_memory_benchmark \
  --benchmark longmemeval-v2-small \
  --sample-percent 10 \
  --sample-mode hash \
  --sample-seed 2026-05-18
```

`prefix` preserves legacy first-N behavior, `hash` gives a stable fixed sample,
and `random` gives a seeded random sample.

Remaining full-reproduction work:

- connect an LLM judge for `llm_abstention_checker` and `llm_gotchas_checker`
- download and pass screenshot files to a vision-capable reader
- compare against any official latency frontier metric if released

The local scorer is deterministic and suitable for CI. A full reproduction may
need the official judge, vision reader, evidence checker, or latency frontier
metric.
