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

Before running the full benchmark, inspect the upstream release schema and map:

- trajectory ids and event ids
- screenshots or multimodal artifacts
- evidence annotations
- latency metrics or LAFS frontier fields
- split names and license constraints

The local scorer is deterministic and suitable for CI. A full reproduction may
need the official judge, evidence checker, or latency frontier metric.
