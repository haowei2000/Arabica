# Changelog

## 0.8.2 — 2026-10-21

- Fix SOE model and thinking selection with one native macOS Policy menu, preserving confirmed values and conversation scope while menus are open.
- Remove repeated main-thread session sorting and ISO 8601 formatter construction by caching the project index and parsing each timestamp once per index update.
- Reuse Markdown block and inline parsing with bounded in-memory caches, reducing repeated work when reading long conversations.

## 0.8.1 — 2026-10-21

- Fix Markdown table sizing compilation with the release Swift toolchain and validate optimized desktop builds in CI.

- Rebuild the macOS sidebar as a native folder and conversation tree with compact rows, persistent expansion, keyboard navigation, and contextual actions.
- Group sidebar, search, and workspace controls in the native title bar and add independent conversation windows with shared library state.
- Simplify the composer to one Policy selector beside Send, with single-model SOE routing and nested model and thinking settings.
- Keep named policies' model routing and thinking levels owned by configuration, while preserving per-model SOE preferences across switches and session resumes.
- Make configuration drafts, save conflicts, model synchronization, and ChatGPT sign-in states explicit; allow Save & Sign in after choosing ChatGPT authentication.
- Improve Markdown tables, nested lists, task lists, block quotes, and supported Mermaid diagrams in conversation messages.
- Add durable conversation renaming and guard sidebar actions, updates, and reconnects while conversations are busy.
- Improve startup configuration errors and preserve session option state during asynchronous switches.

## 0.7.0 — 2026-10-20

- Add dynamic upstream model discovery for OpenAI, ChatGPT OAuth, Anthropic, and Google Gemini providers.
- Add `arabica models list`, `discover`, and `sync` commands to inspect available models and register them directly into user configuration.
- Automatically discover and configure models on ChatGPT sign-in.
- Support dynamic model catalog reloading in ACP sessions and option updates without restarting the agent host.
- Add "Sync Provider Models" button and live model catalog refresh in the desktop app.

## 0.6.1 — 2026-10-18

- Add signed CLI self-updates, daily background updates for interactive standalone installations, opt-out controls, and a version command.
- Add Sparkle desktop updates with automatic checks, installation on quit, update settings, and relaunch deferral during active agent runs.
- Publish signed CLI manifests and architecture-specific signed desktop appcasts from the release workflow.

## 0.6.0 — 2026-10-17

- Add ChatGPT account login in the CLI and desktop app using the official OAuth flow, protected credential storage, and automatic token refresh.
- Serialize credential refresh and revocation across desktop and CLI processes.
- Configure the default Responses provider and model capabilities automatically after login.
- Add shared CLI and desktop controls for model aliases, Blend policies, and supported thinking levels, with workspace preferences and per-model thinking settings.
- Preserve Blend routing when resuming CLI sessions and isolate ACP configuration by workspace and session.
- Include a daily development Blend example with separate planning, execution, and repair models.
- Reconstruct streamed Responses output when terminal events omit output and correct the strict memory search tool schema.
- Build manual releases from the requested tag and include changelog entries in release notes.

## 0.5.1 — 2026-10-13

- Optimize runtime execution speed with SQLite WAL journaling and connection pooling.
- Add thread-local compiled regex caching and memory monotonic sequence numbering to eliminate disk and compilation hot-path overhead.
- Pool HTTP connections with keep-alive and TCP_NODELAY across model provider turns to reduce initial request latency.
- Add security sandboxing defenses: expand credential segment scrubbing and block catastrophic commands (fork bombs, root deletion, raw block device writes, and root permission destruction).
- Implement line-boundary aware dual-ended tool output truncation to protect model context without truncating code tokens, JSON syntax, or error call stacks.
- Introduce actionable 3-step loop recovery guidance in `ToolLoopGuard` to assist model self-healing when read-only tool cycles are detected.

## 0.5.0 — 2026-10-10

- Add typed trace routing, persisted run evidence, acceptance scoring, and policy comparison campaigns.
- Add session-scoped desktop run state and permission notifications.
- Fix ACP permission requests with string identifiers and improve tool details.
- Add desktop themes, configurable fonts, project navigation, and tool icons.
- Upgrade toml, rusqlite, and sha2 while preserving checked SQLite conversions and stable hashes.
- Preserve the frozen historical dataset checksum ledger and case lists from develop.
- Retain the Rust runtime and single active Rust experiment package; legacy Python runners remain retired.
