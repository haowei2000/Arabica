# `structure-cli`

`structure-cli` builds one binary, `structure`, with two ways to drive the
same coding agent: `structure acp` (Agent Client Protocol v1 over stdio, for
editors) and `structure -p` (one-shot execution, for scripts and CI). Both
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

Both entry points resolve the model provider the same way, sharing
`structure-server`'s environment variable names so one provider configuration
works for either host unchanged:

| Variable | Flag override | Required | Purpose |
|---|---|---|---|
| `OPENAI__API_KEY` | *(none)* | yes | Credential. Deliberately has no flag: a `--api-key` argument would put the key in shell history and process listings. |
| `OPENAI__BASE_URL` | `--base-url` | yes | Provider endpoint, e.g. `https://api.openai.com/v1`. |
| `OPENAI__MODEL` | `--model` | yes | Model name. |
| `STRUCTURE__API_TYPE` | `--api-type` | no (default `open_ai_chat_completions`) | `open_ai_chat_completions`, `open_ai_responses`, `anthropic_messages`, `gemini_generate_content`, `gemini_interactions`. |

A flag always overrides its matching variable. Nothing is read from a `.env`
file in the project directory: a malicious repository could otherwise smuggle
its own `OPENAI__BASE_URL` into a session and capture the real API key on the
first model call.

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
      "command": "/absolute/path/to/structure",
      "args": ["acp"]
    }
  }
}
```

`OPENAI__API_KEY` and the rest of the configuration above must already be set
in the environment Zed itself launches from (e.g. exported in your shell
profile) — nothing reads them from Zed's own settings, and there is no field
for them there.

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

### What is not implemented yet

Session persistence, `session/load`, and `session/resume` do not exist: each
ACP session lives only as long as its connection. `AgentCapabilities` reports
`loadSession: false` accordingly. `mcpServers` in `session/new` are ignored
with a warning on stderr — tools run in-process, not over MCP.

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
