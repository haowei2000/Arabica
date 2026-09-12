# Rust experiment suite

The only maintained experiment implementation is the Cargo workspace package
`structure-short-memory-benchmark` in [runtime-short-memory/](runtime-short-memory/README.md).
It shares the production Runtime, Session, Provider, Runner and protocol crates.

## Offline checks

```bash
cargo test -p structure-short-memory-benchmark
make benchmark-smoke
make test-proxy
```

These checks need no model credentials or paid model calls. Node.js is required
only for CLI comparison proxy tests and JavaScript task fixtures.

## Entry points

- Default binary: deterministic traces, projection correctness and scaling.
- `tier_b`: fixture or model-backed end-to-end tasks.
- `cli_comparison`: external CLI comparisons, with fixtures, prompts,
  verifiers and an API proxy in `cli-comparison/`.
- `long_horizon_experiment`: Rust manifests and qualification.
- `harbor_agent`: Rust external-agent binary and transport protocol.
  The Python Harbor bridge, preflight and campaign controller were removed;
  automated Harbor orchestration is no longer provided by this repository.

The Python service, standalone memory harness, dataset loaders and scorers
were removed. Historical LongMemEval and LoCoMo results do not represent
runnable evaluations in the current suite.

## Evidence and local artifacts

`PROTOCOL.md` and `protocol/` preserve dated experiment decisions.
[Saved results](runtime-short-memory/results/README.md), `../reports/` and
`../paper/` preserve historical evidence, not fresh validation of this release.
Local datasets, `../benchmark_runs/` and `../.local/` remain ignored and are
not deleted by source cleanup.

Run new experiments with explicit inputs and retain the source commit, exact
binary, checksum, configuration and outputs together.
