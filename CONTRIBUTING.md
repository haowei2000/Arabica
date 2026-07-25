# Contributing to Structure

Thanks for helping improve Structure. This guide keeps contributions reviewable
and safe for a project that handles credentials, model calls, and user data.

## Development Setup

1. Install Python 3.12+ and uv.
2. Install dependencies:

```bash
uv sync
```

3. Create a local environment file:

```bash
uv run sync-env
```

4. Start local infrastructure and run migrations:

```bash
make docker-up-infra
make db-upgrade
```

5. Run the API and workers as needed:

```bash
make start-api
make start-worker
```

## Quality Checks

Run focused checks before opening a pull request:

```bash
make lint
make format-check
make test
```

For larger changes, also run:

```bash
make test-coverage
```

## Pull Requests

- Keep changes focused and explain the behavior change.
- Add or update tests for user-facing behavior, state transitions, and shared
  services.
- Do not commit real credentials, private endpoints, local editor state, or
  generated output.
- Follow Conventional Commits, for example `fix(auth): reject expired tokens`.

## Security-Sensitive Changes

If your change touches authentication, authorization, storage, model credentials,
tool execution, sandboxing, or deployment, call that out in the PR description
and include the validation you ran.
