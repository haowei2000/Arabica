# Running Arabica under an ACP connector

This page is for platforms (such as an AgentNexus Connector) that launch
`arabica acp` as a stdio child and pass each session a per-session HTTP MCP
endpoint. The behaviour below is covered by the executable-level suite
`crates/arabica-cli/tests/acp_stateless_mcp_acceptance.rs`. That suite drives
the real binary with a deterministic model and an audited MCP 2026-07-28
fixture.

The first release with this behaviour is **v0.12.0** (tag `v0.12.0`, commit
`ae8dcd52`).

## Launch

```json
{"transport": "stdio", "command": "/absolute/path/to/arabica", "args": ["acp", "--mcp-protocol", "modern"], "cwd": "/absolute/path/to/workspace"}
```

- **`args`.** `["acp"]` is enough. Add `--mcp-protocol modern` to refuse
  servers that do not speak MCP 2026-07-28; see the protocol section below.
- **Environment.** Set `ARABICA_HOME` for configuration and session storage,
  and the provider key variable, for example
  `ARABICA_PROVIDER_OPENAI_API_KEY`. Several processes may share one
  `ARABICA_HOME`. A session is held by one process at a time.
- **Output.** stdout carries only newline-delimited JSON-RPC 2.0, and
  diagnostics go to stderr. When stdin reaches EOF, the process abandons
  in-flight HTTP requests and exits with status 0. It waits at most 5 seconds
  to flush pending output. If the client has stopped reading stdout, it logs
  that and exits anyway.
- **Initialize response.** It reports ACP protocol version 1 and
  `agentInfo` `{"name":"arabica","version":"<release>"}`. It advertises
  `loadSession: true`, `mcpCapabilities: {"http": true, "sse": false}`, and the
  session `list`, `resume`, and `close` capabilities.

## Session MCP configuration

Standard ACP `mcpServers` entries are used as they arrive:

```json
{"name": "agentnexus", "type": "http", "url": "http://127.0.0.1:28888/mcp/session/<opaque>", "headers": []}
```

- **Opaque endpoint.** The URL is opaque, and static headers are sent
  unchanged on every request. Arabica adds no `Authorization` header and
  derives no identity from the name, path, or prompt.
- **Session-scoped clients.** Each session has its own MCP clients and tool
  routes, even when two sessions use the same server name. Tools are listed
  once per session, following pagination, and reused across its prompts.
- **Probe sessions.** `mcpServers: []` is accepted for capability-probe
  sessions.
- **User-config servers.** Servers declared in `$ARABICA_HOME/config.toml`
  (`[[mcp]]`) are added to every session; on a name clash the client's entry
  wins. To expose only platform tools, give the connector an `ARABICA_HOME`
  that declares no `[[mcp]]` servers. Arabica never writes MCP configuration
  to `config.toml`.
- **Secrets.** Capability URLs and header values do not appear in model
  requests, persisted session history, stderr diagnostics, or error
  messages. The model sees tool names, descriptions, and input schemas.

## Tool approval

Every MCP tool call asks the client first with `session/request_permission`.
It offers allow once, allow for the session, reject once, and reject for the
session. The connector must answer these requests, or the call never runs.

## Prompts, cancellation, and failures

- **One prompt at a time.** Prompts in one session are serialized. A
  `session/prompt` sent while another is running is rejected at once with
  JSON-RPC `invalid_request` ("a prompt is already in progress for this
  session"), and the running prompt continues.
- **No execution deadline.** There is no default total execution deadline. A
  long, silent tool call keeps the prompt open until the call settles. The
  prompt's single terminal response is sent only after every tool call of
  the turn has settled; nothing is sent to MCP servers after it.
- **Cancellation.** `session/cancel` abandons the in-flight model or HTTP
  request and settles the prompt with `stopReason: "cancelled"`. A tool call
  that was still open is reported `failed`, with the note that it may still
  have taken effect. Nothing is replayed.

How a failed `tools/call` is reported:

| Upstream answer | What happens |
| --- | --- |
| HTTP 4xx (including 401 and 403), a JSON-RPC error, or a result with `isError: true` | The call did not succeed. Its tool update is `failed`, and the model sees the reason and the turn continues. |
| HTTP 5xx, a timeout, a dropped connection, or a broken stream | The outcome is unknown, so the call may have run. It is not retried. Its tool update is `failed`, and the prompt ends with a JSON-RPC internal error ("MCP tool call outcome is unknown and it was not retried"), so the model cannot repeat it. |

Arabica never retries a call without credentials and never starts OAuth for
these servers. A `401` or `403` never causes a protocol downgrade.

## MCP protocol

Arabica uses the official Rust SDK (`rmcp`) and prefers MCP 2026-07-28. On a
modern server there is no `initialize` or `notifications/initialized`, no
`Mcp-Session-Id`, and no standalone GET stream. Every POST carries its own
protocol-version metadata. JSON and request-scoped SSE responses are both
supported.

| `--mcp-protocol` | HTTP servers | Stdio servers |
| --- | --- | --- |
| `auto` (default) | Probe with `server/discover`. Use the legacy `initialize` handshake only when the server rejects discovery with a legacy JSON-RPC error. A slow, failed, or `401`/`403` discovery fails the connection and never downgrades. | The SDK's automatic mode. A legacy stdio server may ignore discovery, so a 10-second probe timeout also selects the legacy handshake. |
| `modern` | MCP 2026-07-28 only. A legacy server fails to connect. | Same. |

A client-supplied server that fails to connect fails `session/new`, `load`, or
`resume` with an error that names the server but not its URL.

## Restoration

`session/load` replays the stored history as `session/update` notifications.
`session/resume` continues without replay. Both connect the `mcpServers`
supplied in that request, so a new capability URL replaces the old one.
History never brings back a previous endpoint, and no prompt or tool call is
replayed. `session/close` releases the session's MCP clients.

## Acceptance coverage

These are local protocol-contract checks: the real executable, a
deterministic model, and an audited fixture.

| Handoff ID | Test |
| --- | --- |
| AR-MCP-01, 02, 03 | `one_session_discovers_tools_and_calls_them_across_two_prompts` |
| AR-MCP-04 | `consecutive_tasks_reuse_one_session_url_without_agent_side_changes` |
| AR-MCP-05 | `setup_and_retired_capabilities_fail_calls_without_effects` |
| AR-MCP-06 | `concurrent_sessions_with_the_same_server_name_stay_isolated` |
| AR-MCP-07, 09 (silence) | `a_slow_silent_call_settles_before_the_terminal_and_nothing_follows` |
| AR-MCP-08 | `error_classes_stay_failures_without_replay_oauth_or_downgrade`, `rejected_or_slow_discovery_never_downgrades`, `legacy_servers_fall_back_by_evidence_and_modern_mode_refuses_them` |
| AR-MCP-09 | `cancel_interrupts_the_call_and_settles_the_prompt_without_replay`, `disconnect_mid_call_exits_without_further_platform_calls` |
| AR-MCP-10 | `restore_binds_the_fresh_capability_and_replays_nothing` |
| AR-MCP-11 | `other_servers_survive_and_capabilities_and_secrets_stay_private` |
| Serialized prompts | `an_overlapping_prompt_is_rejected_and_the_first_still_settles` |

AR-MCP-04 and AR-MCP-07 are checked only from the Agent's side: the same URL
and headers for every Task, and no request after the prompt terminal.
Attributing requests to a Task is the connector's job. Platform integration
with a real Connector and Core, and a real-model smoke test, are separate
acceptance levels that this suite does not cover.
