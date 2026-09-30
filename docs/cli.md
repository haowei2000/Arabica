# `arabica-cli`

`arabica-cli` builds one binary, `arabica`, with three ways to drive the
same coding agent: `arabica` (interactive terminal chat), `arabica acp`
(Agent Client Protocol v1 over stdio, for editors), and `arabica -p`
(one-shot execution, for scripts and CI). All three
bind the same canonical protocol (`docs/protocol.md`) to the same
`CoreRuntime`/`LocalRunner` composition (`crates/arabica-cli/src/host.rs`);
neither is a second implementation of the agent. See
`docs/runtime_core_architecture.md` Appendix B for the design record.

## Build

```bash
cargo build -p arabica-cli --release
```

The binary is at `target/release/arabica`.

## Configuration

CLI and ACP load the named provider catalog from `$ARABICA_HOME/config.toml`
(default `~/.arabica/config.toml`). Each alias selects a provider with its own
API dialect, endpoint, credential, and generation settings.

| Variable | Flag override | Required | Purpose |
|---|---|---|---|
| `ARABICA_PROVIDER_<NAME>_API_KEY` | *(none)* | per provider unless TOML `api_key` is configured | Provider credential; `api_key_env` may specify another variable. |
| `ARABICA__BASE_URL` | `--base-url` | no | Temporary endpoint override for the default alias's provider. |
| `ARABICA__MODEL` | `--model` | no | Temporary model ID override for the default alias. |
| `ARABICA__API_TYPE` | `--api-type` | no | Temporary API dialect override for the default alias's provider. |

A flag always overrides its matching variable. The configured `[blend]`
default alias is selected unless a saved workspace alias overrides it.
`structure config` validates and prints the resolved provider/model catalog
and the config file paths.

Example user config:

```toml
[providers.openai]
api_type = "open_ai_responses"
base_url = "https://api.openai.com/v1"
api_key_env = "ARABICA_PROVIDER_OPENAI_API_KEY"
max_tokens = 8192
thinking = "high"

[providers.anthropic]
api_type = "anthropic_messages"
base_url = "https://api.anthropic.com/v1"
api_key_env = "ARABICA_PROVIDER_ANTHROPIC_API_KEY"
request_timeout_secs = 180
anthropic_cache_static_prefix = true

[models.fast]
provider = "openai"
model_id = "gpt-4.1-mini"

[models.strong]
provider = "anthropic"
model_id = "claude-sonnet-4-5"

[blend]
default_model = "fast"
after_tool_error = "strong"
tool_call_capable_models = ["fast", "strong"]
```

If a provider table contains `api_key`, restrict `config.toml` to your account with
`chmod 600 ~/.arabica/config.toml`; the CLI refuses to read a key from a
more permissive file or a symlink. Alternatively, run `arabica auth login`
once to enter the key without echoing it. The key is then saved in `$ARABICA_HOME/auth.json`
(default `~/.arabica/auth.json`) with owner-only file permissions. Run
`structure auth status` to see which credential source is active, or
`structure auth logout` to remove the separately saved key. Provider
environment variables take precedence over TOML `api_key`; `auth.json` is a
saved-key fallback for the default provider. Existing shared `[provider]`,
`OPENAI__*`, and `ARABICA__MODELS` configurations must be migrated to named
providers and model aliases.

Workspace config files reject an `api_key` field. Nothing is read from a `.env` or `.arabica` directory
in the project: a malicious repository could otherwise redirect model calls
and capture the real API key. ACP exposes configured aliases and the applicable
thinking controls as per-session options.

## Project instructions

Every surface (terminal chat, `arabica acp`, `arabica -p`) sends the
same project instructions to the model. An `AGENTS.md` file in the workspace
root is loaded as a standing system instruction, annotated with its source
path so the model request records where the text came from. When the session
directory sits deeper than the workspace root, `AGENTS.md` files along the
way apply too, root first: the deepest file is rendered last and takes
precedence when two files disagree. Missing, unreadable, or non-UTF-8 files
are skipped silently.

Instructions are re-read before each turn (each ACP prompt, each terminal
message, each `-p` invocation), so editing `AGENTS.md` mid-session reaches
the next model request. New session files record an `instructions_sha256`
header field -- a hash of the instructions in effect at creation, for
tooling that wants a cheap "did the effective instructions change" signal.

### Streaming

Terminal chat streams model output as it arrives for the
`open_ai_chat_completions`, `anthropic_messages`, and
`gemini_generate_content` API types: assistant text appears incrementally,
thinking output streams to the thinking view, and `-p`/ACP receive progress
through the same path. Other API types currently fall back to one-shot
completion and print the answer once it is complete.

## Interactive terminal

Run `arabica` (or `arabica chat`) in a project directory to start a
continuous conversation. In a terminal, the chat runs inline, Claude-Code
style: the transcript is printed straight into the terminal's native
scrollback -- normal scrolling, selection, and search keep working -- while a
small input box pinned to the bottom owns the editor and status line.
Tool calls render folded: one summary line per call, a status line for the
result, and a colored diff for file edits; Ctrl+O toggles verbose printing
for later events. When stdin or stdout is redirected, the simple line-based
interface remains available. `--plain`
selects that interface explicitly. Each prompt uses the same session and event
log; `/exit` or `/quit` ends the terminal process. `--continue` restores the most
recent session in the current directory, and `--resume <ID>` restores a
specific one (`arabica sessions list` shows IDs). Inside a running terminal
chat, `/sessions` lists this workspace's stored sessions and `/resume <id>`
switches to one in place: the new session opens first (an unknown id leaves
the current session untouched), the previous one is suspended, and the next
message continues the resumed session's history. `/resume` without an id
lists resumable sessions; type a line number or an id prefix to pick one.

CLI and ACP sessions use the runtime's file-backed context projection. Older
eligible evidence may be archived under
`$ARABICA_HOME/runtime-memory/<workspace-id>/` (default
`~/.arabica/runtime-memory/`); archives are never written into the project.
The archive stores exact runtime events for `memory_search` and `memory_read`
to recover when needed. The original session event log remains intact.
Archive preparation runs on a background worker while model requests continue.
A request uses only archives completed by an earlier worker pass; pending
batches remain in full context. `/context` shows whether preparation is still
running and reports the last error observed at a model-step boundary.
Archival is subject to the runtime's cost gate, so a short conversation may
produce no archive files.

```bash
arabica
arabica --continue
arabica --allow-shell
arabica --plain
```

In the TUI, typing `/` completes commands, `/thinking ` or `/model ` offers
configured choices, and `@` searches workspace file paths (respecting ignore
rules). Use Up/Down to select a suggestion; Tab or Enter accepts it. Left/Right
always move the editor cursor. Enter sends, Shift+Enter inserts a line. The
composer grows with a draft up to one third of the terminal height and eight
rows, then scrolls internally. Up/Down move by visual row in multiline or
wrapped drafts while keeping the target column; otherwise they navigate
transcript blocks. File suggestions insert a path reference for the agent to
inspect with its read tool; they do not inline the file contents. The file
index refreshes after each turn. `/find <text>` searches the transcript; F3 and
Shift+F3 move to the next and previous result, and ordinary letters remain
available for typing. Escape closes one temporary state at a time (completion,
search, then history selection) and does nothing when idle without one. During
a run Escape or Ctrl+C cancels the run; during permission confirmation Escape
denies that request. The history pane reflows source text when the terminal is
resized. PageUp/PageDown and the mouse wheel scroll it; Ctrl+End returns to the
latest output. New output is marked while reviewing history, and sending a
message or queueing one returns to the live end. Copying a user message uses
its original text and line breaks. Ctrl+C clears a nonempty draft and exits
when the draft is empty. Ctrl+D deletes the character after the cursor, or
exits when the draft is empty. Ctrl+A/E move to the current line's start/end,
Ctrl+←/→ move by word, Ctrl+U/K delete from the cursor to the current line's
start/end, and Ctrl+W or Ctrl+Backspace deletes the word before the cursor.
Messages typed during a run are queued for the next turn. `/help` lists
commands. `/session` shows the current session and model. `/diff` reports the agent's net file changes this session, as unified
diffs, and marks files that have changed on disk since the agent's last write.
`/undo` reverts the agent's most recent write call (`write_file`, `edit_files`,
or `delete_file`; one `edit_files` call counts as one change even when it edits
several files). Undo is all-or-nothing per call: when any file it touched no
longer matches what the call left behind -- because you edited it, for example
-- nothing is reverted, the file is left untouched, and the checkpoint stays
for a later retry. Only the local write tools are checkpointed: writes made by
MCP tools are not undoable, and the journal lives in process memory, so a
resumed session starts with an empty one. `/context` opens a scrollable view of the last recorded model request,
provider-reported token usage, a policy preview for the next turn, and the
workspace archive count. The preview excludes the next message and FileBackedGC
admission; it is not a token estimate. Escape or `q` closes the view. The
archive count covers the workspace, not just the current session. `/model <alias>` and
`/thinking <off|on|low|medium|high>` change the
configured Blend alias or its thinking mode for the next prompt and persist those choices for this workspace. Selecting an alias can switch both provider and model. Chat
Completions supports `off` and `on`; Responses supports `off`, `low`, `medium`,
and `high`.

Read-only tools run without a prompt. File changes require a terminal
approval. The chooser defaults to **Allow once**; Enter confirms the selected
option. Use the arrow keys and Enter, or `y` (allow once), `a` (allow this
session), `n` (deny once), and `v` (deny this session). Escape denies only the
current request; Ctrl+C cancels the run. The labels state whether the decision
applies once or for the session. On narrow terminals the choices are shown
vertically and remain visible while PageUp/PageDown or the mouse wheel scrolls
the tool details in the history pane. The prompt shows what the call targets --
the write path with a content preview for `write_file`, old → new strings for
`edit_files` -- above the choices. In full-screen mode, click a history block
and press Enter or `c` to copy it; the mouse wheel scrolls history, and
Ctrl+End returns to the latest output.
`memory_search` and `memory_read` are read-only recovery tools.
Shell is available only with `--allow-shell` and also requires approval.
`--read-only` excludes mutating tools and cannot be combined with
`--allow-shell`. Ctrl-C cancels a running turn; at the input prompt it exits.

## `arabica acp`

Speaks [Agent Client Protocol](https://agentclientprotocol.com) v1 over
stdio. `initialize` always answers protocol version 1 with no auth methods
(`authMethods: []`): credentials come from provider key environment variables
or the user config. Every `session/new` builds its own runtime and its own
session manager, so concurrent sessions (multiple editor windows, multiple
projects) do not share state or serialize behind one lock.

### Zed

Add to Zed's `settings.json`:

```json
{
  "agent_servers": {
    "Arabica": {
      "type": "custom",
      "command": "/absolute/path/to/arabica",
      "args": ["acp"],
      "env": {
        "ARABICA_PROVIDER_OPENAI_API_KEY": "<your-api-key>",
        "ARABICA_PROVIDER_ANTHROPIC_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

Zed passes `env` to the agent process. These values can also be inherited
from Zed's launch environment, in which case the `env` object may be omitted.
Putting provider keys in `settings.json` stores them as plain text; use a
secure launch environment if that is unacceptable. The aliases and endpoints
come from `~/.arabica/config.toml`. Zed's own model-provider settings do not
configure Structure's providers.

New and restored sessions expose ACP model and thinking selectors. The model
selector contains configured aliases. Chat
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

`arabica acp` advertises `read_file`, `list_dir`, `grep`, `find_files`,
`write_file`, `edit_files`, `delete_file`, and `shell`
(`crates/arabica-cli/src/main.rs`'s `run_acp`). The read-only tools run
without asking; everything else — including `shell`, which has no path
confinement and is gated by approval alone — goes through a real
`session/request_permission` round trip before it runs
(`crates/arabica-cli/src/acp/permission.rs`). Four options are offered:
allow once, allow for the rest of the session, reject once, reject for the
rest of the session — all four are enforced by the runtime's permission gate,
not just displayed.

### Sessions and MCP tools

ACP sessions are saved under `$ARABICA_HOME` (or its default location) as
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

### User-configured MCP servers

Terminal chat and `-p` read MCP servers from the user config file
(`$ARABICA_HOME/config.toml`, default `~/.arabica/config.toml`), so the
same servers work in the terminal without an editor client supplying them:

```toml
[[mcp]]
name = "docs"
command = "/usr/local/bin/docs-mcp"   # stdio transport
args = ["--verbose"]

[mcp.env]
DOCS_TOKEN = "..."                    # credentials require chmod 600

[[mcp]]
name = "search"
url = "https://mcp.example.test/mcp"  # streamable HTTP transport

[mcp.headers]
Authorization = "Bearer ..."
```

Exactly one of `command` (stdio) or `url` (HTTP) selects the transport.
ACP clients can supply servers too; when a client and the config file
declare the same name, the client's entry wins. A server that fails to
connect is reported on stderr and skipped -- one broken entry does not take
the session down. Diagnostics name the server and the failure, never a
credential value. Every MCP tool call goes through the same permission
prompt (terminal) or `session/request_permission` (ACP) as the other
surface, and server configuration, especially `env` and `headers` values,
is never written to the session log.

### ACP verification

`cargo test -p arabica-cli` covers a real `arabica acp` subprocess with
an ACP client and mock model provider, multi-turn message ordering, permission
requests, ACP load/resume, and print-mode cross-process session recovery. The
in-process ACP client test
`client_supplied_stdio_mcp_tool_is_discovered_approved_and_executed` connects
to a stdio MCP fixture, approves a tool, and checks its side effect. The HTTP
MCP test verifies discovery, a client-supplied request header, and a tool
call. After building the binary, configure Zed as above and verify a new
session, a tool approval, and reopening the session after restarting Zed.

## `arabica -p`

Runs one task non-interactively and exits. There is no client to ask for
permission in this mode, so it never wires a permission gate at all: what the
tool policy admits runs outright, and what it excludes is invisible to the
model.

```bash
arabica -p "add a CHANGELOG entry for the last commit"
echo "summarize open TODOs in src/" | arabica -p -
```

### Flags

| Flag | Effect |
|---|---|
| `-p, --print <TASK>` | The task text, or `-` to read it from stdin. |
| `--output-format text\|jsonl` | `text` (default): stdout is only the final answer (nothing at all if the run didn't complete); progress goes to stderr. `jsonl`: stdout streams one JSON `EventEnvelope` per line, live, for every client-visible Event — the same boundary `arabica acp` uses, so no internal model exchange (system prompt, raw reasoning) ever reaches a pipe built for scripting. |
| `--allow-shell` | Adds `shell` to the tool policy. Off by default. |
| `--read-only` | Restricts the policy to read-only tools. Mutually exclusive with `--allow-shell` (exit code 2 if both are given): `shell` has no path confinement, so allowing it while also promising nothing changes would make the promise false, not just permissive. |
| `--continue` | Continue the most recently active session in this workspace. |
| `--resume <ID>` | Resume a specific session in this workspace; see `arabica sessions list`. |

Each print-mode run is saved in the same event-log format as ACP. Use
`arabica sessions list` to see sessions for the current workspace, or
`arabica sessions list --all` to see every workspace. Listing does not
require model-provider credentials.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | The run completed. |
| `1` | The run failed (provider error, runtime error). |
| `2` | Configuration error: missing credentials, or `--allow-shell` with `--read-only`. |
| `130` | Cancelled (Ctrl-C). The first Ctrl-C cancels the in-flight run; a second exits immediately. |

### Example: piping arabicad output

```bash
arabica --output-format jsonl -p "run the test suite and report failures" \
  | jq -r 'select(.event.type == "tool.call.completed") | .event.payload.name'
```

## Workspace identity

Both entry points derive a `WorkspaceId` from the session's working directory
(`sha256(cwd)`'s first 16 hex characters, prefixed `ws-`;
`crates/arabica-cli/src/host.rs::workspace_id_for`). The same project
directory produces the same workspace id regardless of which binding opened
it or how many times.
