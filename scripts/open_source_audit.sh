#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CURRENT_TREE_ONLY=0
if [[ "${1:-}" == "--current-tree-only" ]]; then
  CURRENT_TREE_ONLY=1
fi

echo "==> Checking required community files"
required_files=(
  "LICENSE"
  "NOTICE"
  "README.md"
  "CONTRIBUTING.md"
  "SECURITY.md"
  "CODE_OF_CONDUCT.md"
  ".github/dependabot.yml"
  ".github/pull_request_template.md"
)

for file in "${required_files[@]}"; do
  test -f "$file" || {
    echo "Missing required file: $file" >&2
    exit 1
  }
done

echo "==> Checking for tracked local artifacts"
tracked_env_files="$(git ls-files | rg '(^|/)\.env($|\.)' | rg -v '(^|/)\.env\.example$' | while IFS= read -r path; do [[ -e "$path" ]] && printf '%s\n' "$path"; done || true)"
tracked_artifacts="$(git ls-files | rg '(^|/)(\.run/|\.zed/|\.output\.txt|__pycache__|.*\.pyc$|.*\.pem$|.*\.key$|.*\.p12$|.*\.crt$|.*\.cert$|id_rsa|id_ed25519)' | while IFS= read -r path; do [[ -e "$path" ]] && printf '%s\n' "$path"; done || true)"
if [[ -n "$tracked_env_files$tracked_artifacts" ]]; then
  printf '%s\n%s\n' "$tracked_env_files" "$tracked_artifacts"
  echo "Tracked local artifact or credential-like file found." >&2
  exit 1
fi

echo "==> Checking current tree for high-confidence secret patterns"
if rg -n -I \
  -g '!uv.lock' \
  -g '!frontend/package-lock.json' \
  -g '!LICENSE' \
  '(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[0-9A-Za-z-]{20,}|-----BEGIN (RSA|OPENSSH|PRIVATE) KEY-----|Bearer eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)' \
  .; then
  echo "Potential secret found in current tree." >&2
  exit 1
fi

echo "==> Checking for internal endpoints and placeholder production credentials"
if rg -n -I \
  -g '!uv.lock' \
  -g '!frontend/package-lock.json' \
  -g '!scripts/open_source_audit.sh' \
  -g '!docs/OPEN_SOURCE_RELEASE.md' \
  '(10\.1\.2\.111|difyai123456|AI630|/Users/.*/PycharmProjects|AUTH__JWT_SECRET_KEY=structure|POSTGRES__PASSWORD=123456|REDIS__PASSWORD=123456|AUTH__ADMIN_PASSWORD=123456|REDIS_COMMANDER_PASSWORD=123456)' \
  .; then
  echo "Internal endpoint or unsafe default found." >&2
  exit 1
fi

echo "==> Checking whether historical cleanup is still needed"
if [[ "$CURRENT_TREE_ONLY" -eq 1 ]]; then
  echo "Skipping Git history scan (--current-tree-only)."
  echo "Open-source current-tree audit passed."
  exit 0
fi

history_scan_file="/tmp/structure-history-secret-scan.txt"
: > "$history_scan_file"
history_secret_found=0
for revision in $(git rev-list --all); do
  if git grep -I -n -E \
    '(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[0-9A-Za-z-]{20,}|-----BEGIN (RSA|OPENSSH|PRIVATE) KEY-----)' \
    "$revision" >> "$history_scan_file" 2>/dev/null; then
    history_secret_found=1
  fi
done

if [[ "$history_secret_found" -eq 1 ]]; then
  echo "Historical credential-like content found. See /tmp/structure-history-secret-scan.txt" >&2
  echo "Follow docs/OPEN_SOURCE_RELEASE.md before making the repository public." >&2
  exit 1
fi

echo "Open-source audit passed."
