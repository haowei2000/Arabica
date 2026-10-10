# Frozen historical dataset artifacts

These files preserve the dataset checksums and 200-case samples promoted from
PR #104. They are historical protocol evidence for the retired Python benchmark,
not inputs to the active Rust short-memory experiment.

- `checksums.txt` contains SHA-256 digests keyed relative to the original data directory.
- `case_lists/` contains the four frozen case lists sampled with seed `20260710`.

The legacy Python runners, integrity gate, and freeze script remain retired.
Do not regenerate these artifacts without a dated protocol amendment. Active
experiments use `benchmarks/runtime-short-memory` and its existing Rust gates.
