# Open Source Release Checklist

This checklist must pass before making the repository public.

## 1. Rotate Exposed Credentials

The repository history previously contained credential-like values and an SSH
private-key filename. Treat them as compromised even if the current tree no
longer contains them.

Rotate or revoke:

- Any OpenAI-compatible API key that may match old `.env.example` values.
- Any DashScope or Alibaba Tongyi key that may match old `.env.example` values.
- Any SSH key previously stored under `docker/ollama-models/id_ed25519`.
- Any database, Redis, object-storage, admin, or deployment credentials copied
  from examples into real infrastructure.

## 2. Rewrite Private History Before Publication

Do this in a disposable clone, not in an active working copy:

```bash
git clone --mirror git@github.com:haowei2000/Structure.git Structure-public-clean.git
cd Structure-public-clean.git

git filter-repo \
  --path docker/ollama-models/id_ed25519 \
  --path docker/ollama-models/id_ed25519.pub \
  --path src/.env.example \
  --path src/.env.test \
  --invert-paths

cat > /tmp/structure-replacements.txt <<'EOF'
OLD_OPENAI_COMPATIBLE_KEY==>REDACTED_OPENAI_COMPATIBLE_KEY
OLD_EXAMPLE_OR_REAL_PASSWORD==>REDACTED_PASSWORD
OLD_INTERNAL_HOST_OR_REGISTRY==>REDACTED_INTERNAL_ENDPOINT
EOF

git filter-repo --replace-text /tmp/structure-replacements.txt
```

Then run the audit against a normal clone of the rewritten repository:

```bash
scripts/open_source_audit.sh
```

For ordinary PR checks before history has been rewritten, use the current-tree
mode:

```bash
scripts/open_source_audit.sh --current-tree-only
```

Only force-push rewritten history after coordinating with every collaborator:

```bash
git push --force --mirror origin
```

If preserving private history is important, create a new public repository from
a clean export instead of force-pushing the existing repository.

## 3. Validate Current Tree

Run:

```bash
scripts/open_source_audit.sh
make lint
make format-check
make test
make benchmark-smoke
make test-proxy
```

## 4. GitHub Repository Settings

Before switching visibility to public:

- Enable private vulnerability reporting.
- Enable Dependabot alerts and security updates.
- Require pull-request review on `main`.
- Require the CI workflow to pass before merge.
- Add repository topics: `ai-agents`, `rust`, `event-sourcing`, `llm`,
  `orchestration`.
- Confirm `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, and issue templates render
  correctly on GitHub.

## 5. Release

Tag public releases with semantic versions:

```bash
git tag -a v0.1.0-rust-only -m "Rust-only implementation and experiment baseline"
git push origin v0.1.0-rust-only
```
