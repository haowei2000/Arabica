# `structure-cli`

`structure-cli` builds one binary, `structure`, with three ways to drive the
same coding agent: `structure` (interactive terminal chat), `structure acp`
(Agent Client Protocol v1 over stdio, for editors), and `structure -p`
(one-shot execution, for scripts and CI). All three
bind the same canonical protocol (`docs/protocol.md`) to the same
`CoreRuntime`/`LocalRunner` composition (`crates/structure-cli/src/host.rs`);
neither is a second implementation of the agent. See
`docs/runtime_core_architecture.md` Appendix B for the design record.

## Build

```bash
cargo build -p structure-cli --release
```

The binary is at `target/release/structure`.

## Configuration

The CLI and ACP share `structure-server`'s environment variable names:

| Variable | Flag override | Required | Purpose |
|---|---|---|---|
| `OPENAI__API_KEY` | *(none)* | yes for ACP; CLI can use saved auth | Credential. Deliberately has no flag: a `--api-key` argument would put the key in shell history and process listings. |
| `OPENAI__BASE_URL` | `--base-url` | yes | Provider endpoint, e.g. `https://api.openai.com/v1`. |
| `OPENAI__MODEL` | `--model` | yes | Model name. |
| `STRUCTURE__API_TYPE` | `--api-type` | no (default `open_ai_chat_completions`) | `open_ai_chat_completions`, `open_ai_responses`, `anthropic_messages`, `gemini_generate_content`, `gemini_interactions`. |
| `STRUCTURE__MODELS` | *(none)* | no | Comma-separated model names offered in the ACP selector. The current `OPENAI__MODEL` is always included. |

A flag always overrides its matching variable. Terminal chat and `-p` also
load `$STRUCTURE_HOME/config.toml` (default `~/.structure/config.toml`) and a
workspace settings file under `$STRUCTURE_HOME/workspaces/<workspace-id>/config.toml`.
For the model, resolution is flag → saved workspace choice → environment →
user config. For endpoint and API type, it is flag → environment → user
config. Thinking is saved workspace choice → user config → off. `structure
config` prints the resolved values and both file paths.

Example user config:

```toml
[provider]
api_type = "open_ai_chat_completions"
api_key = "your-api-key"
base_url = "https://api.openai.com/v1"
model = "gpt-4.1"
thinking = "off"
```

If `config.toml` contains `api_key`, restrict it to your account with
`chmod 600 ~/.structure/config.toml`; the CLI refuses to read a key from a
more permissive file or a symlink. Alternatively, run `structure auth login`
once to enter the key without echoing it. The key is then saved in `$STRUCTURE_HOME/auth.json`
(default `~/.structure/auth.json`) with owner-only file permissions. Run
`structure auth status` to see which credential source is active, or
`structure auth logout` to remove the separately saved key. Credential
priority is `OPENAI__API_KEY` → user `config.toml` → `auth.json`. ACP still requires its environment
variable, so editor clients keep their existing credential configuration.

Workspace config files reject an `api_key` field. Nothing is read from a `.env` or `.structure` directory
in the project: a malicious repository could otherwise redirect model calls
and capture the real API key. ACP retains its environment-and-flag resolution
and its per-session model and thinking controls.

## Interactive terminal

Run `structure` (or `structure chat`) in a project directory to start a
continuous conversation. In a terminal, this opens a full-screen TUI with a
scrollable transcript, a multi-line editor, model/thinking status, streamed
responses, tool activity, and permission prompts. When stdin or stdout is
redirected, the simple line-based interface remains available. `--plain`
selects that interface explicitly. Each prompt uses the same session and event
log; `/exit` or `/quit` ends the terminal process. `--continue` restores the most
recent session in the current directory, and `--resume <ID>` restores a
specific one (`structure sessions list` shows IDs).

CLI and ACP sessions use the runtime's file-backed context projection. Older
eligible evidence may be archived under
`$STRUCTURE_HOME/runtime-memory/<workspace-id>/` (default
`~/.structure/runtime-memory/`); archives are never written into the project.
The archive stores exact runtime events for `memory_search` and `memory_read`
to recover when needed. The original session event log remains intact.
Archive preparation runs on a background worker while model requests continue.
A request uses only archives completed by an earlier worker pass; pending
batches remain in full context. `/context` shows whether preparation is still
running and reports the last error observed at a model-step boundary.
Archival is subject to the runtime's cost gate, so a short conversation may
produce no archive files.

```bash
structure
structure --continue
structure --allow-shell
structure --plain
```

In the TUI, typing `/` completes commands, `/thinking ` or `/model ` offers
configured choices, and `@` searches workspace file paths (respecting ignore
rules). Use Up/Down and Tab or Enter to choose a suggestion; enter again to
send. File suggestions insert a path reference for the agent to inspect with
its read tool; they do not inline the file contents. The file index refreshes
after each turn. Enter sends, Shift+Enter or Ctrl+J inserts a line, PageUp/PageDown
scrolls the transcript, Ctrl+T shows or hides thinking, and Escape cancels a
running turn. Ctrl+C exits at the editor and cancels a running turn. Messages
typed during a run are queued for the next turn. The TUI restores the terminal
screen on exit. `/help` lists commands. `/session` shows the current session and
model. `/context` opens a scrollable view of the last recorded model request,
provider-reported token usage, a policy preview for the next turn, and the
workspace archive count. The preview excludes the next message and FileBackedGC
admission; it is not a token estimate. Escape or `q` closes the view. The
archive count covers the workspace, not just the current session. `/model <name>` and
`/thinking <off|on|low|medium|high>` change the
provider for the next prompt and persist those choices for this workspace. Chat
Completions supports `off` and `on`; Responses supports `off`, `low`, `medium`,
and `high`. The model name must be supported by the configured endpoint.

Read-only tools run without a prompt. File changes require a terminal
approval (`y` once, `a` for the session, `n` once, or `v` for the session).
For `write_file`, the TUI previews line changes against an existing regular
workspace file when it is small enough to inspect. It also shows the complete
tool arguments in a focused permission view; use
Up/Down or PageUp/PageDown to inspect long content before deciding.
`memory_search` and `memory_read` are read-only recovery tools.
Shell is available only with `--allow-shell` and also requires approval.
`--read-only` excludes mutating tools and cannot be combined with
`--allow-shell`. Ctrl-C cancels a running turn; at the input prompt it exits.

## `structure acp`

Speaks [Agent Client Protocol](https://agentclientprotocol.com) v1 over
stdio. `initialize` always answers protocol version 1 with no auth methods
(`authMethods: []`): there is nothing to authenticate beyond the environment
variables above. Every `session/new` builds its own runtime and its own
session manager, so concurrent sessions (multiple editor windows, multiple
projects) do not share state or serialize behind one lock.

### Zed

Add to Zed's `settings.json`:

```json
{
  "agent_servers": {
    "Structure": {
      "type": "custom",
      "command": "/absolute/path/to/structure",
      "args": ["acp"],
      "env": {
        "OPENAI__API_KEY": "<your-api-key>",
        "OPENAI__BASE_URL": "https://api.openai.com/v1",
        "OPENAI__MODEL": "<model-name>",
        "STRUCTURE__MODELS": "<model-name>,<another-model>"
      }
    }
  }
}
```

Zed passes `env` to the agent process. These values can also be inherited
from Zed's launch environment, in which case the `env` object may be omitted.
Putting `OPENAI__API_KEY` in `settings.json` stores it as plain text; use a
secure launch environment if that is unacceptable. Zed's own model-provider
settings do not configure Structure's provider.

New and restored sessions expose ACP model and thinking selectors. The model
selector contains `STRUCTURE__MODELS` plus the starting model. Chat
Completions has `off` and `on` thinking levels; Responses has `off`, `low`,
`medium`, and `high`. A switch applies to the next prompt in that session.
The Chat Completions adapter sends a nonstandard `thinking` parameter when
enabled, so the endpoint must support it. In ACP sessions it requests Chat
Completions streaming and forwards text and `reasoning_content` deltas as
message and thought updates. A provider that returns ordinary JSON instead
of SSE still works, with updates emitted when the response completes. The
complete model response is retained in the session event log for subsequent
turns; streamed chunks are not duplicated on completion. Other provider
adapters still emit their ACP updates after complete HTTP responses.

### Tools and permissions

`structure acp` advertises `read_file`, `list_dir`, `grep`, `find_files`,
`write_file`, `edit_files`, `delete_file`, and `shell`
(`crates/structure-cli/src/main.rs`'s `run_acp`). The read-only tools run
without asking; everything else — including `shell`, which has no path
confinement and is gated by approval alone — goes through a real
`session/request_permission` round trip before it runs
(`crates/structure-cli/src/acp/permission.rs`). Four options are offered:
allow once, allow for the rest of the session, reject once, reject for the
rest of the session — all four are enforced by the runtime's permission gate,
not just displayed.

### Sessions and MCP tools

ACP sessions are saved under `$STRUCTURE_HOME` (or its default location) as
append-only event logs. The agent supports `session/list`, `session/load`
(replays history), `session/resume` (continues without replay), and
`session/close` (suspends the session so it can be opened again). A client
may supply MCP servers in `session/new`, `session/load`, or `session/resume`;
each request reconnects to its supplied servers and discovers their tools.
Server configuration, especially environment variables and HTTP headers, is
not written to the session log.

Stdio and streamable HTTP MCP transports are supported. Stdio commands must
be absolute paths. Legacy SSE MCP transport is rejected with an error;
the ACP capability advertises HTTP support but not SSE support. An MCP tool
is exposed to the model as `mcp__SERVER__TOOL` (non-alphanumeric characters
become underscores). Name collisions and connection failures fail session
creation rather than silently hiding tools. MCP resources and prompts are not
exposed to the model yet. Every MCP tool call requires an
ACP `session/request_permission` decision, even if the server advertises a
read-only hint.

The MCP client uses the official Rust SDK (`rmcp`). A stdio server receives
only a small base environment (`PATH`, `HOME`, temporary-directory and locale
variables) plus the `env` entries supplied by the ACP client; it does not
inherit the model provider's API key. HTTP requests use the headers in the
client's server configuration.

### ACP verification

`cargo test -p structure-cli` covers a real `structure acp` subprocess with
an ACP client and mock model provider, multi-turn message ordering, permission
requests, ACP load/resume, and print-mode cross-process session recovery. The
in-process ACP client test
`client_supplied_stdio_mcp_tool_is_discovered_approved_and_executed` connects
to a stdio MCP fixture, approves a tool, and checks its side effect. The HTTP
MCP test verifies discovery, a client-supplied request header, and a tool
call. After building the binary, configure Zed as above and verify a new
session, a tool approval, and reopening the session after restarting Zed.

## `structure -p`

Runs one task non-interactively and exits. There is no client to ask for
permission in this mode, so it never wires a permission gate at all: what the
tool policy admits runs outright, and what it excludes is invisible to the
model.

```bash
structure -p "add a CHANGELOG entry for the last commit"
echo "summarize open TODOs in src/" | structure -p -
```

### Flags

| Flag | Effect |
|---|---|
| `-p, --print <TASK>` | The task text, or `-` to read it from stdin. |
| `--output-format text\|jsonl` | `text` (default): stdout is only the final answer (nothing at all if the run didn't complete); progress goes to stderr. `jsonl`: stdout streams one JSON `EventEnvelope` per line, live, for every client-visible Event — the same boundary `structure acp` uses, so no internal model exchange (system prompt, raw reasoning) ever reaches a pipe built for scripting. |
| `--allow-shell` | Adds `shell` to the tool policy. Off by default. |
| `--read-only` | Restricts the policy to read-only tools. Mutually exclusive with `--allow-shell` (exit code 2 if both are given): `shell` has no path confinement, so allowing it while also promising nothing changes would make the promise false, not just permissive. |
| `--continue` | Continue the most recently active session in this workspace. |
| `--resume <ID>` | Resume a specific session in this workspace; see `structure sessions list`. |

Each print-mode run is saved in the same event-log format as ACP. Use
`structure sessions list` to see sessions for the current workspace, or
`structure sessions list --all` to see every workspace. Listing does not
require model-provider credentials.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | The run completed. |
| `1` | The run failed (provider error, runtime error). |
| `2` | Configuration error: missing credentials, or `--allow-shell` with `--read-only`. |
| `130` | Cancelled (Ctrl-C). The first Ctrl-C cancels the in-flight run; a second exits immediately. |

### Example: piping structured output

```bash
structure --output-format jsonl -p "run the test suite and report failures" \
  | jq -r 'select(.event.type == "tool.call.completed") | .event.payload.name'
```

## Workspace identity

Both entry points derive a `WorkspaceId` from the session's working directory
(`sha256(cwd)`'s first 16 hex characters, prefixed `ws-`;
`crates/structure-cli/src/host.rs::workspace_id_for`). The same project
directory produces the same workspace id regardless of which binding opened
it or how many times.
