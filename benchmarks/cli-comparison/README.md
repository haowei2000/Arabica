# PiAgent vs Codex CLI pilot

This suite independently checks two small repository tasks with hidden verifier
scripts. Each surface receives the same fixture, prompt, model, thinking level,
timeout, and repeat schedule. Workspaces are fresh and trial order is
deterministically randomized within each pair.

The runner records typed results, file-scope changes, normalized usage, duration,
and SHA-256 digests of stdout/stderr. It intentionally does not retain raw model
transcripts. Write manifests and reports outside this directory because the
manifest freezes the complete suite tree digest.

Planning and preflight never call a model:

```bash
cargo run -p structure-short-memory-benchmark --bin cli_comparison -- plan \
  --suite benchmarks/cli-comparison/suite.json \
  --output /tmp/structure-cli-comparison/manifest.json \
  --model openai/gpt-5.6-sol \
  --thinking xhigh \
  --piagent-package-root /path/to/piagent

cargo run -p structure-short-memory-benchmark --bin cli_comparison -- preflight \
  --manifest /tmp/structure-cli-comparison/manifest.json
```

Running a trial can consume provider quota and therefore requires `--yes`:

```bash
cargo run -p structure-short-memory-benchmark --bin cli_comparison -- run \
  --manifest /tmp/structure-cli-comparison/manifest.json \
  --trial-id falsey-config-r01-piagent \
  --output-root /tmp/structure-cli-comparison \
  --yes
```

Only paired trials where both surfaces pass are included in the fresh-token
ratio. Quality non-inferiority is required before a token improvement is
reported. This two-task suite is a local pilot, not a general product claim.
