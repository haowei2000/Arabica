# Loop–Runner Protocol (LRP)

**Status:** Phase 1 implemented (§12); later phases are design
**Proposed source of truth:** `crates/arabica-protocol` (`lrp` module)
**Transport binding:** WebSocket over TLS 1.3 with mutual authentication
**Wire version:** `lrp.v1`

## 1. Purpose

The Loop–Runner Protocol separates the agent loop from tool execution. The
*Loop* is a composition host (`arabica-server` or `arabica-cli`) that owns the
Runtime, the model calls, sessions, and the permission gate. A *Runner* is a
separate process, usually on another machine, that owns a workspace and
executes typed tool calls inside it.

One Loop may hold connections to many Runners. One Runner serves one Loop.

LRP carries only what `RunnerEnvironment` already exchanges in process:
tool definitions, classification, `ToolCallItem` in, ordered stdout/stderr
and `ToolResultItem` out, and cancellation. It never carries conversation
history, model credentials, memory, or session state.

### Non-goals

- Model traffic. Model Provider calls stay on the Loop.
- Session or event-log replication. Runners are stateless apart from their
  workspace.
- A general remote shell. Runners execute only the tools they advertise.
- Replacing MCP. MCP servers remain tool sources attached to the Loop or
  to a Runner (§11).

## 2. Ownership

| Concern | Owner | Must not own |
|---|---|---|
| Tool selection, permission gate, event sequencing | Loop | Workspace files or process execution |
| Runner registry, routing, leases | Loop (`RunnerGateway`) | Runner-local policy |
| Execution, confinement, resource limits, scrubbing | Runner | Conversation, model credentials |
| Which tools exist and how they are limited | Runner (authoritative) | Session scheduling |
| Wire types and version | Protocol | Domain behavior |

The Runner's own `LocalRunnerPolicy` is authoritative. A Loop may request
less than a Runner allows (for example a read-only lease), never more. A
compromised Loop can therefore do no more on a Runner than that Runner's
local configuration permits.

## 3. Topology

```text
            ┌────────────────────── Loop host ───────────────────────┐
model API ◄─┤ Runtime ──► RoutingRunner ──► RunnerGateway (wss://)   │
            └───────────────────────────────▲─────────▲─────────▲────┘
                                            │ outbound wss + mTLS
                                        runner-a  runner-b  runner-c
                                        LocalRunner + local policy
```

Runners dial out to the Loop. Runner machines need no inbound port and work
behind NAT and firewalls. The connection is long-lived and multiplexes every
lease and call for that Runner.

A Loop that cannot accept inbound connections may instead dial a Runner
listening on `wss://`. The protocol is identical after the handshake; only
the TCP direction changes.

## 4. Security

### 4.1 Transport

- TLS 1.3 only, via rustls. No plaintext `ws://` outside of tests bound to
  loopback.
- WebSocket subprotocol `arabica.lrp.v1` must be negotiated; a server that
  does not echo it closes the connection.
- Maximum frame size 1 MiB, maximum message size 4 MiB. Larger tool output is
  truncated by the Runner using the existing `MAX_TOOL_OUTPUT_CHARS` policy
  before it is framed.

### 4.2 Identity

Each Runner has an X.509 client certificate issued by the Loop's runner CA.
The certificate's URI SAN is `arabica-runner://<runner_id>`. The Loop
authenticates the Runner by mTLS; the Runner authenticates the Loop by
pinning the Loop's CA (or server certificate fingerprint) configured at
enrollment.

`runner_id` in `hello` must equal the certificate SAN or the Loop closes the
connection with `unauthenticated`.

### 4.3 Enrollment

1. An operator creates a join token on the Loop:
   `arabica runner token create --workspace ws-1 --label os=linux --ttl 15m`.
   Tokens are single-use, short-lived, and bound to the grants they list.
2. The Runner generates its key pair locally and sends a CSR with the token
   to the Loop's enrollment endpoint over TLS (`POST /runners/enroll`).
3. The Loop returns a signed certificate (default lifetime 30 days), the CA
   bundle, and the assigned `runner_id`. The private key never leaves the
   Runner.
4. Runners renew before expiry over an authenticated LRP connection
   (`cert.renew`). Revocation deletes the `runner_id`; the Loop rejects it on
   the next handshake and closes any live connection.

### 4.4 Authorization

Every `runner_id` has grants stored on the Loop:

```toml
[runners.runner-a]
workspaces = ["ws-1", "ws-2"]
labels = { os = "linux", gpu = "true" }
max_concurrent_calls = 8
```

The Loop opens a lease only for a workspace the Runner is granted. The
Runner independently maps `workspace_id` to a local root and refuses unknown
workspaces with `workspace_unknown`. Both sides must agree.

### 4.5 Integrity and replay

- `call_id` is unique per lease. A Runner rejects a repeated `call.start`
  with the same `call_id` and returns the recorded outcome instead (§8.2).
- Every Loop→Runner request carries a monotonic `seq` per connection. A
  Runner rejects a non-increasing `seq` with `protocol_error`.
- Model credentials, MCP credentials held by the Loop, and conversation
  content are never sent to a Runner. Runners scrub credential-named
  environment variables exactly as `LocalRunner` does today.

## 5. Framing

All messages are JSON text frames with a common envelope:

```json
{
  "v": "lrp.v1",
  "type": "call.start",
  "id": "m-42",
  "seq": 42,
  "body": {}
}
```

| Field | Rule |
|---|---|
| `v` | Wire version; must match the negotiated version. |
| `type` | Message type from §6. Unknown types are a `protocol_error`. |
| `id` | Present on requests; echoed as `reply_to` on the response. |
| `seq` | Present on Loop→Runner requests; strictly increasing per connection. |
| `body` | Type-specific payload. Unknown fields are ignored. |

Responses use `reply_to` instead of `id`. Notifications (`call.output`,
`heartbeat`) carry neither.

Binary frames are reserved for a future file-transfer extension and are a
`protocol_error` in `lrp.v1`.

## 6. Messages

### 6.1 Handshake

`hello` (Runner → Loop, first frame):

```json
{
  "runner_id": "runner-a",
  "versions": ["lrp.v1"],
  "runner_version": "0.13.0",
  "labels": { "os": "linux", "arch": "x86_64" },
  "capacity": { "max_concurrent_calls": 8 },
  "workspaces": ["ws-1"],
  "tools": [
    {
      "definition": { "name": "shell", "description": "...", "parameters": {} },
      "classification": { "kind": "dynamic", "classifier": "shell" }
    },
    {
      "definition": { "name": "read_file", "description": "...", "parameters": {} },
      "classification": { "kind": "static", "interaction": "inspection" }
    }
  ],
  "resumable_calls": []
}
```

`welcome` (Loop → Runner, reply to `hello`):

```json
{
  "version": "lrp.v1",
  "connection_id": "c-7f3a",
  "heartbeat_interval_ms": 15000,
  "heartbeat_timeout_ms": 45000,
  "features": ["tools.changed"]
}
```

`features` lists optional messages the Loop understands; omitted means
none. A Runner sends an optional message only to a Loop that listed it, so
an older Loop is never sent a type it would reject (§5).

`RunnerEnvironment::classify` is synchronous, so classification is resolved
on the Loop from `hello`. A `static` tool always maps to one
`ToolInteractionKind`. A `dynamic` tool names a pure classifier that ships
in `arabica-runner` (today only `shell`, backed by
`classify_shell_interaction`). A Runner may not ship a classifier the Loop
does not know; unknown classifiers fall back to `generic`.

`tools.changed` (Runner → Loop notification, feature `tools.changed`)
replaces the tool list after the handshake, for example when the Runner's
policy is changed or a Runner-side MCP server connects:

```json
{
  "tools": [ /* every tool the Runner now offers, as in hello.tools */ ],
  "leases": { "l-91": ["read_file", "edit_files"] }
}
```

- `tools` replaces `hello.tools` for this connection.
- `leases` gives the new effective tool names of every lease open on this
  connection. The Runner re-derives each one from its new policy with the
  lease's original `mode` and `tools` request, so a change can restore a
  tool the lease asked for but can never add one it did not ask for.
- The Runner enforces the new lease tools immediately, whether or not the
  Loop asked for the notification. A `call.start` for a withdrawn tool is
  rejected with `tool_unavailable`; calls already accepted finish under the
  policy they started with.
- The Loop re-advertises the tools to the model between runs. A run keeps
  the tool list it started with, so a call the model makes to a withdrawn
  tool during that run comes back as a tool error.

### 6.2 Leases

A lease binds one Loop session to one workspace on one Runner. All calls
belong to a lease.

`lease.open` (Loop → Runner):

```json
{
  "lease_id": "l-91",
  "session_id": "s-12",
  "workspace_id": "ws-1",
  "mode": "coding",
  "tools": ["read_file", "edit_files", "shell"]
}
```

`mode` and `tools` may only narrow the Runner's policy. The reply is
`lease.opened` with the effective tool list, or an error.

`lease.close` (Loop → Runner) cancels every running call in the lease and
releases it. Runners close leases themselves when the connection is lost
for longer than the resume window (§8.2).

### 6.3 Calls

`call.start` (Loop → Runner):

```json
{
  "lease_id": "l-91",
  "run_id": "r-301",
  "call": {
    "id": null,
    "call_id": "call_abc",
    "name": "shell",
    "arguments": { "command": "cargo test -p arabica-runner" }
  },
  "deadline_ms": 600000,
  "checkpoint": true
}
```

`call` is exactly `arabica_model::ToolCallItem`. `provider_state` is
stripped by the Loop before sending.

Runner replies, in order:

1. `call.accepted` (reply to `call.start`) once the call is scheduled, or an
   error such as `capacity_exhausted` or `tool_unavailable`.
2. Zero or more `call.output` notifications:

   ```json
   { "call_id": "call_abc", "chunk": 0, "stream": "stdout", "text": "running 12 tests\n" }
   ```

   `chunk` is strictly increasing per call. The Loop maps each chunk
   one-for-one to `RunnerOutput::Stdout` / `RunnerOutput::Stderr`, preserving
   the ordering guarantee in `protocol.md`.
3. Exactly one terminal message:
   - `call.completed` with `result` (exactly `arabica_model::ToolResultItem`)
     and, when `checkpoint` was requested and files changed, `movements`
     (§6.4).
   - `call.failed` with an error (§9) when the Runner could not produce a
     tool result at all.

A tool that ran and failed (non-zero exit, bad arguments) is a
`call.completed` with `result.is_error = true`, matching `LocalRunner`.

### 6.4 Checkpoints

The CLI's `/diff` and `/undo` need before/after snapshots of the files a
mutating call touched. With `checkpoint: true`, the Runner snapshots paths
named by the call (same rule as `checkpoint::mutating_paths`) and returns:

```json
"movements": [
  { "path": "src/lib.rs", "before_sha256": "…", "after_sha256": "…", "before": "<base64>", "after": "<base64>" }
]
```

`before`/`after` are omitted for files over 256 KiB; the Loop then records
the movement as non-restorable. `checkpoint.restore` (Loop → Runner) writes
a recorded `before` back, verifying `after_sha256` first and failing with
`conflict` if the file moved since.

### 6.5 Cancellation

`call.cancel` (Loop → Runner) with either `call_id` or `run_id`. The reply
`call.cancel_ack` reports `{ "cancelled": true | false }`. A cancelled call
still ends with exactly one terminal message, normally `call.failed` with
`cancelled`. This maps directly to `RunnerEnvironment::cancel(run_id)`.

### 6.6 Liveness and shutdown

- `heartbeat` notifications flow in both directions every
  `heartbeat_interval_ms`. Missing heartbeats for `heartbeat_timeout_ms`
  marks the connection lost.
- `drain` (Runner → Loop): the Runner accepts no new leases or calls, lets
  running calls finish, then closes. The Loop routes new sessions elsewhere.
- `cert.renew` (Runner → Loop): CSR in, certificate out (§4.3).

## 7. Multiple runners

The Loop keeps a `RunnerRegistry` of connected Runners: identity, grants,
labels, advertised tools, capacity, in-flight calls, and last heartbeat.

`RoutingRunner` implements `RunnerEnvironment`, so `CoreRuntime` and
`arabica-session` are unchanged. It routes in this order:

1. **Host-local tools.** Plan tools and Loop-attached MCP tools never leave
   the Loop, as in `HostRunner` today.
2. **Per-tool route.** An explicit rule wins, for example
   `shell -> label gpu=true`. Routes are optional and must target a Runner
   that also has the session's workspace.
3. **Session lease.** Otherwise the call goes to the Runner holding the
   session's lease.

Lease placement when a session starts:

```toml
[sessions.runner]
workspace = "ws-1"
selector = { os = "linux" }   # all labels must match
prefer = "least_loaded"       # or "sticky" to reuse the last Runner
fallback = "fail"             # or "local" to use the in-process LocalRunner
```

A session stays on its Runner because workspace state lives there. The Loop
never moves a lease to another Runner on its own; a lost Runner surfaces as
failed tool calls and a `run.failed` if the run cannot continue.

`cancel(run_id)` fans out to every Runner holding calls for that run and
returns `true` if any of them cancelled something.

## 8. Reliability

### 8.1 Backpressure

`capacity.max_concurrent_calls` bounds in-flight calls per Runner. The Loop
queues beyond that and fails queued calls after `deadline_ms`. Output is
already bounded per call by the Runner's truncation policy, so no
credit-based flow control is defined in `lrp.v1`.

### 8.2 Reconnect and resume

If a connection drops, the Runner keeps running in-flight calls and keeps
leases and terminal outcomes for a resume window (default 120 s). On
reconnect its `hello` lists `resumable_calls` with each call's state:

```json
"resumable_calls": [
  { "lease_id": "l-91", "call_id": "call_abc", "state": "running", "next_chunk": 17 },
  { "lease_id": "l-91", "call_id": "call_def", "state": "completed" }
]
```

The Loop sends `call.resume { call_id, from_chunk }` for calls it still
waits on; the Runner replays buffered output from `from_chunk` and the
terminal message. Calls the Runner does not list are reported to the Runtime
as `call.failed` with `runner_lost`.

The Loop never re-sends `call.start` for a call that may have reached the
Runner. Mutating tools are not retried automatically under any condition.

## 9. Errors

Errors are `{ "code": "...", "message": "...", "retryable": bool }`.

| Code | Meaning | Retryable |
|---|---|---|
| `unauthenticated` | Certificate or `runner_id` mismatch | no |
| `forbidden` | Workspace or tool outside the Runner's grants | no |
| `version_unsupported` | No common LRP version | no |
| `protocol_error` | Malformed frame, unknown type, bad `seq` | no |
| `workspace_unknown` | Runner has no root for `workspace_id` | no |
| `tool_unavailable` | Tool not offered on this lease | no |
| `capacity_exhausted` | Runner at `max_concurrent_calls` | yes |
| `deadline_exceeded` | Call ran past `deadline_ms` | no |
| `cancelled` | Call was cancelled | no |
| `conflict` | Checkpoint restore found a moved file | no |
| `runner_lost` | Connection lost past the resume window | no |
| `internal` | Unexpected Runner failure | no |

The Runtime normalizes these into `RunnerError` and the existing
`run.failed` / tool-result events; no new Event types are required.

## 10. Rust mapping

| Piece | Location |
|---|---|
| LRP message types, version, error codes, JSON Schema export | `arabica-protocol::lrp` |
| `RemoteRunner: RunnerEnvironment` (one Runner connection) | new `arabica-runner-remote` |
| Runner-side server wrapping `LocalRunner` | `arabica-runner-remote`, exposed as `arabica runner serve` |
| `RunnerGateway`, `RunnerRegistry`, `RoutingRunner`, enrollment endpoint | `arabica-server` and `arabica-cli` hosts |

`arabica-runner-remote` depends on `arabica-runner`, `arabica-model`, and
`arabica-protocol`, plus `tokio-tungstenite` with rustls. It must not
depend on `arabica-provider`, `arabica-runtime`, or `arabica-session`,
keeping the dependency rule in `protocol.md` intact.

## 11. Relation to MCP and ACP

LRP is deliberately MCP-shaped (`tools` advertisement, call, progress,
cancel) but is its own protocol because it needs things MCP does not
define: Runner-initiated connections, workspace leases, typed ordered
stdout/stderr, checkpoints, resume, and Loop-side classification.

- A Runner may attach its own MCP servers and advertise their tools over
  LRP; those tools then run next to the workspace.
- An ACP client that offers `fs/*` and `terminal/*` is a different Runner
  backend (`AcpClientRunner`) behind the same `RunnerEnvironment`; it does
  not use LRP.

## 12. Phases

1. **Single Runner.** Message types, `RemoteRunner`, `arabica runner serve`
   with a statically configured certificate, Loop dials out. Conformance
   tests against an in-process Runner.
2. **Runner dial-out and multiple Runners.** `RunnerGateway`, registry,
   leases, `RoutingRunner`, placement config.
3. **Enrollment.** Join tokens, CSR flow, renewal, revocation, Runner status
   in CLI and desktop.
4. **Checkpoints and resume.** `/diff` and `/undo` over LRP; resume window.

### Phase 1 as implemented

Code: `arabica-protocol::lrp` (wire types), `arabica-runner-remote`
(`RemoteRunner`, `RunnerHost`, `TlsIdentity`), and the CLI's
`arabica runner serve` and `--runner <name>`.

Runner machine:

```bash
arabica runner serve --listen 0.0.0.0:7443 --runner-id build-box \
  --workspace repo=/srv/repo \
  --cert runner.pem --key runner.key --client-ca loop-ca.pem [--allow-shell]
```

Loop machine, `$ARABICA_HOME/config.toml`, then `arabica --runner build-box`
(chat) or `arabica --runner build-box -p "..."`:

```toml
[runners.build-box]
url = "wss://build-box.internal:7443/lrp"
workspace = "repo"
runner_id = "build-box"          # optional; must match the runner's hello
ca_cert = "certs/runner-ca.pem"  # relative paths resolve against ARABICA_HOME
client_cert = "certs/loop.pem"
client_key = "certs/loop.key"    # must be chmod 600
call_timeout_secs = 900          # optional
```

Differences from the full design, to close in later phases:

- The loop dials the runner (§3's alternative direction); runner dial-out,
  the registry, and multiple runners are phase 2. The runner verifies the
  loop's client certificate; the loop verifies the runner's server
  certificate by name and, optionally, its announced `runner_id`. Binding
  `runner_id` to a certificate SAN arrives with enrollment.
- `call.output` chunks are sent when the call finishes, not while it runs,
  because `LocalRunner` returns output at the end. Order and the chunk
  numbering are already as specified.
- No resume window: when a connection drops, the runner aborts its calls
  and the loop reports `runner_lost`.
- A repeated `call_id` on a lease is rejected with `protocol_error`
  instead of returning the recorded outcome.
- No checkpoints: `/diff` and `/undo` do not see remote writes.
- A lease never exceeds the loop's local policy: the CLI leases exactly the
  tools `--read-only` / `--allow-shell` would allow locally, intersected
  with the runner's own policy.
- A full runner (`capacity_exhausted`) is retried with backoff until the
  call deadline, then reported to the model as a tool error.
- Project instructions and the system prompt still describe the loop's
  working directory.
- `tools.changed` is sent when an embedding process calls
  `RunnerHost::set_policy`. `arabica runner serve` takes its policy from
  flags and has no reload trigger yet. The interactive CLI applies a
  change before the next turn; `-p` runs a single turn and keeps its tools.

## 13. Open questions

- Should leases support more than one workspace per session (monorepo
  split across machines), or is that a per-tool route?
- Is a 120 s resume window right for long builds on flaky networks, or
  should it scale with `deadline_ms`?
- Should the Loop's runner CA be separate from any CA used for MCP or the
  HTTP + SSE binding? (Proposed: yes.)
- Does `arabica-server --profile service` need per-tenant runner CAs?
