# Long-horizon paired experiment protocol

`long_horizon_experiment` freezes trial identity and order before any provider
call. Qualification uses the five candidate tasks in fixed order and at most
two adjacent B0/FBGC blocks per task. The first three tasks that meet the
verifier and FBGC archive-recovery gate become the primary task set.

Primary runs use five B0/FBGC blocks per frozen task. Each block is AB or BA,
deterministically selected from the manifest seed. Runs are serial and each
trial has a distinct report directory. The primary analysis uses only blocks
where both official verifiers pass; infrastructure failures and length
truncation remain in the ledger and are never silently discarded.

Create a qualification manifest:

```text
cargo run -p structure-short-memory-benchmark --bin long_horizon_experiment -- \
  plan --phase qualification --output results/qualification-manifest.json \
  --seed 20260809 --provider longcat --model LongCat-2.0
```

Create the primary or Anthropic replication manifests with `--phase primary`
or `--phase replication` and a comma-separated `--tasks` value. Replication
contains B0, FBGC, and CAPC, with CAPC restricted to `anthropic_messages`.

CAPC marks only the invariant system and tool-schema prefix using Anthropic
`cache_control`. Runtime history, tool output, memory pointers, and task state
are never included in that cache block. Reports separately record cache reads
and cache creation tokens, so cache writes cannot be mistaken for savings.

The paper mechanism matrix is: Memex—external full evidence plus indexed
dereference; DPM—event log plus task-conditioned projection; CAPC—stable
prefix compression and cache control; Structure—relation-aware event
materialisation, semantic evidence gates, hash-verified archive recovery, and
online GC admission.
