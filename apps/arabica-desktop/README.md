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
sessions in that folder can be loaded or resumed from the sidebar; the context
menu closes a session. ACP v1 tool calls show
their category, status, text results, diffs, terminal references, and file
locations. Write and command calls use native permission dialogs. The client
also handles ACP file and terminal requests within the chosen workspace.
The paperclip adds files as ACP resource links to a prompt.
Settings owns persistent provider, default model, and default policy choices
in `~/.arabica/config.toml`. It never displays a saved API key and writes the
configuration with owner-only permissions. Multiple provider/model catalogs,
policy routes, and MCP definitions remain available through the advanced
config file opened from Settings. The Composer shows the current session's ACP
model, policy, and thinking options, plus session modes when the host reports
them; changing those controls does not rewrite the saved defaults.

## Scope of this version

The client displays text messages, reasoning, plans, configuration options,
available commands, usage, and tool calls. Rich Markdown, image attachments,
and distribution signing are later work. The desktop client
speaks ACP v1 and accepts unknown `_meta` extension fields without making
assumptions about their contents.
