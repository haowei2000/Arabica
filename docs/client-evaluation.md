# Evaluation queries for CLI, ACP, and desktop clients

All clients use the same versioned `EvaluationView` JSON envelope. It contains
`schema_version`, workspace and session IDs, optional worker counters,
`refresh_accepted`, and an optional paired context/model report. The report's
checkpoint sequence is its freshness marker. A missing report means no cached
or persisted report is available; it does not indicate Agent failure. Worker
counters are scoped to the evaluation worker and aggregate across its sessions.

## CLI

```bash
arabica evaluations show SESSION_ID --json
arabica evaluations show SESSION_ID --workspace-id WORKSPACE_ID --json
```

This command resolves before provider configuration, needs no API key, and does
not start an Agent or an evaluator. It reads an existing SQLite database in
read-only mode. A missing database returns a JSON envelope with `report: null`,
without creating files. A busy or incompatible database produces exit code 1;
combining this command with `-p` produces exit code 2. Successful queries,
including no-report results, use exit code 0. Human-readable output is available
without `--json`.

Interactive `/context` continues to show worker status and the last cached
report, while requesting background refresh. Use the JSON command for desktop
wrappers and scripts that need structured output rather than terminal text.

## ACP

The initialize response advertises the optional `arabica.evaluation` capability
under `_meta`, with schema version, method names, and asynchronous behavior.
These are Structure-specific extensions; standard ACP prompt, cancel, permission,
and session methods retain their behavior.

```json
{"jsonrpc":"2.0","id":1,"method":"_arabica/evaluation/get","params":{"sessionId":"ACP_SESSION_ID"}}
{"jsonrpc":"2.0","id":2,"method":"_arabica/evaluation/refresh","params":{"sessionId":"ACP_SESSION_ID"}}
```

`get` reads the worker's nonblocking cache. Worker initialization may replay
persisted evaluations in the background. `refresh` attempts to enqueue a session
refresh and immediately returns `refresh_accepted: true` or `false`; acceptance
means queued, not complete. History loading and latest terminal checkpoint
selection happen on the evaluation thread, never inside the request handler.
If the session has no terminal run, there is nothing to evaluate yet.

Requests resolve ACP session IDs only against sessions registered to this ACP
connection. They cannot select arbitrary workspaces, paths, or database files.
Unknown sessions and invalid parameters return an invalid-params error. Neither
method acquires the running session manager lock, calls a provider, or waits for
evaluation. Clients can query while prompts run and continue to process cancel
and permission exchanges. A desktop client should show the report checkpoint,
refresh acceptance, and counters rather than treating acceptance as completion.

## HTTP read-only bridge

The headless HTTP host can expose reports written by CLI/ACP for desktop clients:

```bash
ARABICA__EVALUATION_HOME="$HOME/.arabica" cargo run -p arabica-server
```

```text
GET /v1/evaluations?workspace_id=WORKSPACE_ID&session_id=SESSION_ID
```

This endpoint is explicitly enabled by `ARABICA__EVALUATION_HOME` or
`AppState::with_evaluation_home`; it is disabled by default and returns 404 with
`evaluation_disabled`. Database reads use a blocking task, a read-only SQLite
connection, and a bounded 50 ms lock timeout. Reads do not acquire the Agent
session lock, mutate/migrate the database, or start evaluation. A missing report
returns 200 with `report: null`; database failures return 503 with
`evaluation_unavailable`, without exposing filesystem errors. HTTP views have
no worker counters or refresh acceptance because this bridge does not own the
CLI/ACP worker. The endpoint is an optional read exposure through the host's
existing transport boundary.

The native macOS client lives in `apps/arabica-desktop` and talks to ACP. Its
collapsible Background evaluation inspector is available from the toolbar or
Option-Command-I. It checks the advertised capability, polls while visible,
preserves cached reports during refresh/failure, and discards responses from
previous sessions or connections. Evaluation errors do not change chat state.

HTTP Agent sessions do not automatically persist JSONL or schedule evaluation;
the HTTP bridge reads the explicitly configured CLI/ACP evaluation database.
