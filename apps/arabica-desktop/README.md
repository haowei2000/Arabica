# Arabica Desktop

The first macOS client uses SwiftUI for the window and ACP v1 over stdio to
connect to the existing `arabica acp` executable. The Rust workspace remains
headless; the desktop package owns only presentation and process management.

## Requirements

- macOS 14 or later
- Xcode Command Line Tools or Xcode with Swift 5.10 or later
- Rust 1.88 or later
- `rsvg-convert` or `resvg` for rendering `static/arabica-icon.svg` into the app icon
- An Arabica provider configuration in `~/.arabica/config.toml` or the
  supported environment variables (see the repository configuration guide)

## Build and run

From this directory:

```sh
bash scripts/package.sh debug
open dist/Arabica.app
```

Use `bash scripts/package.sh` for an unsigned local release build. The script
builds the Swift client and the Rust CLI, then places both in `dist/Arabica.app`.
GitHub tag releases additionally sign, notarize, and staple the application
before publishing the ZIP. See [RELEASING.md](RELEASING.md) for the required
GitHub Actions credentials. For local Swift
development, `swift build` and `swift test` work independently; set
`ARABICA_EXECUTABLE` to the absolute path of a built `arabica` binary when
running the Swift executable outside the application bundle.

Choose a workspace folder, create a conversation, and send a message. Previous
sessions can be found with the toolbar search and loaded or resumed. The native
window toolbar groups Toggle Sidebar, Search Conversations, Open Workspace, and
Choose Workspace. Option-Command-S toggles the sidebar; Shift-Command-F opens
search, and Command-O opens a workspace folder. Drafts and attachments
are kept separately for each conversation while the app is running. Streaming
replies follow the latest message until you scroll back; **Jump to Latest**
resumes following the conversation. ACP v1 tool calls show
their category, status, text results, diffs, terminal references, and file
locations. Write and command calls use native permission dialogs. The client
also handles ACP file and terminal requests within the chosen workspace.
The paperclip adds files as ACP resource links to a prompt.
Drag the sidebar divider to resize it, or double-click to reset its width.
The sidebar uses a native `NSOutlineView`: workspace folders contain conversation
children with compact single-line titles. Arrow keys navigate the tree; Left and
Right collapse and expand folders, and Return activates **Show more**. Each folder
initially shows five conversations. Paths and full status descriptions are available
in tooltips and accessibility help. Folder disclosure keeps the current conversation
open. Row actions appear on hover or selection and are also available by right-click;
New Conversation remains in the top toolbar and each folder's menu. Running, approval,
error, pinned, and unread indicators stay compact. Choose **View Options → Show Archived
Conversations** to browse the archive. Narrow windows temporarily collapse the
sidebar while retaining search and expansion state.
Filter conversations by title, project, path, or ID. Pin conversations within a project, rename them, or move them
between Active and Archived; both pinning and archiving persist as shared
application preferences. Removing a project only hides it from the sidebar.
Closing a conversation releases its connection while preserving its history
and in-memory draft. Durable rename uses the optional ACP session extension;
older hosts leave Rename disabled. See [client session metadata](../../docs/client-sessions.md).
Opening another project reuses the ACP connection and keeps background tasks
running. Client file and terminal requests retain their originating session's
workspace, including after approval. Sidebar rows distinguish loading, running,
approval, failure, completion, and cancellation, and keep unread background
results until visited. Busy sessions and projects are protected from sidebar
organization actions across windows. Sending in an archived conversation
restores it to Active.

The composer places one **Policy** menu next to Send or Stop, below the message
field. It has no persistent status banner. Connection, loading, and option-change
progress appears on the relevant action; configuration errors remain available in
its help and the conversation error area. **SOE** is a single-model policy that
uses the selected model for every step. Its model submenu contains Thinking Level.
Named policies use model routing and thinking levels from the configuration file;
the composer exposes neither per-model nor thinking overrides for them. Changing
policies preserves SOE's per-model preferences for the next switch back. Sending
waits for confirmation of a policy change, and stopping remains pending until the
run finishes. You can keep writing the next draft while a run is active.

Shift-Command-N opens an independent conversation window with its own ACP
connection, selected conversation, and drafts. Menu commands target the focused
conversation window. Settings reconnects the most recently active conversation
window; appearance preferences remain application-wide. Closing a conversation
window stops its associated ACP process.
Settings owns persistent provider, default model, and default policy choices
in `$ARABICA_HOME/config.toml` (or `~/.arabica/config.toml` by default). Choose
**Save Changes** to write connection and routing drafts; switching entries or
reloading prompts you to save or discard outstanding changes. Appearance
changes save automatically. Settings never displays a saved API key and writes the
configuration with owner-only permissions. Multiple provider/model catalogs,
policy routes, and MCP definitions remain available through the advanced
config file opened from Settings. The composer's Policy menu includes session
modes when the host reports them; changing its controls saves workspace preferences
without rewriting the provider defaults. The bundled runtime supports the
current ChatGPT authentication configuration, including `auth = "chatgpt"`;
configuration rejection messages identify a safe line and column and direct you
to Settings.

## Scope of this version

The client displays text messages, reasoning, plans, configuration options,
available commands, usage, and tool calls. Markdown includes inline formatting,
ATX and Setext headings, nested lists and quotes, task indicators, indented and
fenced code, and horizontally scrollable tables.
Mermaid blocks preview common flowcharts and sequence diagrams locally; other
diagram syntax remains available as source with an explanation. Image attachment
previews remain future work. The desktop client
speaks ACP v1 and accepts unknown `_meta` extension fields without making
assumptions about their contents.

## Background evaluation inspector

Use **View Options → Show Evaluation Panel** in the toolbar or **Option-Command-I** to
show the collapsible inspector. The panel reads the optional ACP evaluation
capability and does not send extensions to older agents. Status queries have a
10-second timeout and run every five seconds while the inspector is visible.
Switching sessions or reconnecting discards responses from the previous scope.

**Request refresh** only queues background work. It keeps the last report visible
and shows its event checkpoint; acceptance never implies completion. Evaluation
errors remain inside the inspector and do not change chat or cancellation state.
The inspector includes context/model observations, routing cohorts, sample
limitations, and worker totals across all conversations. These metrics are not
a correctness score or causal contribution estimate.
