# Parallel local development

Use one branch and one worktree per task. Keep the main checkout available for
integration, and use PRs and CI before retiring task worktrees.

Install `just`, Worktrunk, sccache, and direnv locally. Worktrunk shell integration
must be active in the interactive shell for `wt switch` to change directories.

```bash
wt switch --create feat/example --base main
just dev-init
direnv allow
just check-pkg arabica-runtime
just test-pkg arabica-runtime
just lint-pkg arabica-runtime
git diff --check
```

The Worktrunk pre-start hook runs the idempotent initializer automatically when
approved. Plain Git worktrees can use `just dev-init`. The initializer assigns
a free HTTP port in 18000..27999, avoiding recorded worktree ports and current
TCP listeners. Allocation is serialized within this repository; another project
may subsequently bind the port, so check listeners if a server cannot start.
Existing `.env.local` files are preserved. Its absolute paths must be regenerated
or edited after moving a worktree. The generated environment is private and
loads through just or direnv; `.env.local` is not automatically read by the server.

Each task uses its ignored `.local/dev-home/` for configuration, SQLite state,
and archives. Only `~/.arabica/config.toml` is copied, with owner-only permissions;
sessions and saved authentication are never copied. Review relative resource
paths and supply required provider keys through the environment. Configure a
new local file if no global configuration exists. For HTTP mode, add the provider
and blend settings from `.env.example` to `.env.local`, then run `just server`.
The CLI uses its local TOML configuration with `just chat`.

```bash
just desktop-package
just desktop-run
```

The desktop recipe starts the bundle executable directly, allowing its ACP
child to inherit task-local variables. Launching an app through Finder does not
load `.envrc` or `.env.local`; use the recipe for isolated development sessions.
There is no PostgreSQL, Redis, or React development service to provision.

Cargo keeps `target/` local to each checkout. Configure sccache and
`incremental = false` at the user level, and put cache capacity/directory settings
in the sccache configuration file so non-interactive launches use the same
settings as terminals. Do not commit machine paths or reduce debug information.
APFS target cloning is optional and deliberately not enabled by default.

Before integration, run `just ci` plus relevant desktop/benchmark checks.
`just inventory` and `just prune-preview` are read-only maintenance commands.
Inspect uncommitted and ignored files, processes, and branch integration before
removal. Back up task-local data that must survive a worktree. Never automate
database deletion or broad cache cleanup. Runtime archives may live under
`target/` in older setups; preserve them before `cargo clean`.
