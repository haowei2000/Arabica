# Structure Multi-Platform Core Architecture

Structure exposes one shared conceptual core through multiple runtime
surfaces. The surfaces are intentionally not identical applications:
they share vocabulary, evidence, and local contracts, while keeping
their runtime responsibilities separate.

## Canonical Core

The canonical cross-platform contract lives in:

- `core/structure_core.json`

This manifest is the source of truth for:

- paper-facing primitives such as path addressing, disclosure, audit
  events, source evaluation, event GC, and reproducible evidence
- product surfaces: web app, desktop app, and CLI/TUI
- surface responsibilities and boundaries
- the cross-surface capability matrix that records implementation status,
  entrypoints, and evidence for each paper/runtime capability
- the benchmark report evidence schema shared by local and service-backed
  benchmark runners

Consumers must treat this manifest as read-only configuration. New
platforms should extend the manifest first, then add a platform adapter.

## Surface Parity Matrix

The manifest's `capabilities` array is the machine-readable answer to
"are the web app, desktop app, and CLI/TUI conceptually consistent?" A
capability binds:

- one paper primitive, such as `event_audit` or `reproducible_evidence`
- a short implementation description
- one status record per surface
- the concrete entrypoint and evidence proving what that surface does

The statuses are intentionally not all identical. `native` means the
surface implements the capability directly in its own runtime;
`service_native` means it implements the same concept through the
backend service; `read_only` means it exposes the shared vocabulary or
evidence without owning the execution path. This keeps the products
conceptually aligned without pretending that CLI/TUI, desktop, and web
serve the same user job.

The local CLI exposes this matrix through:

```text
uv run structure parity --json
uv run structure parity --verify --json
structure-local parity --json
structure-local parity --verify --json
```

`uv run structure ...` is a Python compatibility entrypoint that delegates to
the Rust CLI/TUI. It uses `STRUCTURE_LOCAL_BIN` when set, then a
`structure-local` binary on `PATH`, and otherwise falls back to
`cargo run --quiet -p structure-local -- ...` from the source checkout.

The verified report checks required surfaces, capability id uniqueness,
primitive references, per-surface coverage, allowed status values, non-empty
entrypoint/evidence fields, and repository source markers for the declared
entrypoints. The desktop app calls the same repo-aware verifier through
`core_parity_report` and displays whether the shared contract passes.

The local runtime can also package the verification trail as a single evidence
bundle:

```text
uv run structure evidence bundle --json
structure-local evidence bundle --json
```

That bundle contains the local snapshot, parity report, workspace replay,
per-run evidence summaries, knowledge source metadata, and artifact records. It
is the local artifact to attach to paper-oriented reproduction notes when the
claim concerns CLI/TUI or desktop behavior rather than the service-backed web
runtime.

Benchmark reports use the shared `BenchmarkReportSchema`, but benchmark
execution is intentionally outside the local app/CLI product surface. Dedicated
adapters under `benchmarks/adapters/` own benchmark runs and evidence relinking,
for example:

```text
uv run python benchmarks/scripts/run_structure_benchmark.py ...
```

This keeps the local CLI/TUI and desktop app focused on Codex-like chat and
code-agent workflows while preserving benchmark evidence as a separate adapter
contract for experiments.

## Runtime Layers

| Layer | Path | Responsibility |
| --- | --- | --- |
| Paper contract | `paper/sections/*.tex` | Explains the research primitives and references the manifest as the artifact contract. |
| Canonical manifest | `core/structure_core.json` | Language-neutral shared vocabulary and surface map. |
| Rust local core | `crates/structure-local-core/src/` | Local filesystem model, manifest parsing, repo discovery, reports, and snapshots. |
| Rust local runtime | `crates/structure-local-runtime/src/` | Embedded SQLite event loop, local workspace context, knowledge registry, allowlisted no-shell command tools, and artifacts. |
| Rust CLI/TUI | `crates/structure-local/src/` | Operator commands and terminal UI backed by local core/runtime. |
| Desktop app | `frontend/src-tauri/` | Tauri shell invoking Rust local core/runtime for local-first agent work. |
| Web app | `frontend/src/` | Service-backed React app that imports the manifest without replacing its API runtime. |
| Backend service | `src/structure/` | FastAPI/event-sourced live agent runtime. |

## Rust Local Core Modules

`structure-local-core` is split by domain:

- `manifest.rs`: parses the canonical manifest and exposes shared
  surface metadata.
- `repo.rs`: finds the Structure repository and environment-defined
  repo roots.
- `reports.rs`: reads repository-local report files safely for tooling that
  needs file previews.
- `snapshot.rs`: composes local status from repo, reports, and manifest
  state.

This crate must not depend on CLI/TUI, Tauri, frontend, or backend
runtime code.

## Rust Local Runtime Modules

`structure-local-runtime` is the embedded agent kernel for local products:

- `store.rs`: SQLite schema and persistence for workspaces, runs, events,
  workspace listing/lookup, and knowledge source metadata.
- `runtime.rs`: local run lifecycle, context loading, artifact writing,
  event sequencing, workspace replay, and local evidence bundle creation.
- `tools.rs`: safe local repository tools, knowledge source reads, and
  allowlisted command execution used by both the CLI/TUI and desktop app.
- `types.rs`: serializable contracts shared by CLI/TUI and Tauri commands.

This crate must not depend on TUI drawing code, Tauri window code, React,
or the Python backend. It is the common local execution layer.

## CLI/TUI Modules

`structure-local` is split by interface:

- `cli.rs`: command parsing and command dispatch.
- `text.rs`: stable text/JSON-oriented presentation helpers.
- `tui.rs`: terminal event loop, drawing code, and local input modes for
  workspace creation, custom agent prompts, and knowledge registration.
- `main.rs`: process entrypoint only.

The CLI should remain scriptable. Commands that emit machine-readable
state should support JSON where practical. Noninteractive agent commands expose
the same local event loop with explicit `--mode chat|code_agent` selection:
`chat` remains useful for conversational runs, while `run` defaults to
code-agent behavior for Codex/OpenCode-style repository work.

## Web Compatibility Rule

The web app remains service-backed. It may import
`frontend/src/core/structureCore.ts` to read the canonical manifest, but
it must not switch live workspace, auth, run, event, or streaming logic
to local filesystem behavior.

## Desktop and CLI Local-Core Rule

The desktop app and CLI/TUI should use `structure-local-core` for local repo
state, manifest parsing, and safe file preview.
They should use `structure-local-runtime` for event-sourced runs, workspace
metadata, workspace-scoped context, knowledge source metadata, and artifacts.
Platform-specific UI code can decide how to present the data, but should not
duplicate local execution logic.
