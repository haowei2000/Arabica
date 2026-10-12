#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
if [[ -e .env.local ]]; then
  echo "Keeping existing .env.local; no configuration overwritten."
  exit 0
fi

# Serialize allocation across this repository's worktrees. An interrupted owner
# leaves a visible lock for manual inspection instead of risking duplicate ports.
common_dir="$(git rev-parse --git-common-dir)"
common_dir="$(cd "$common_dir" && pwd)"
lock_dir="$common_dir/arabica-dev-env.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  echo "Environment initialization is locked at $lock_dir; retry after its owner finishes." >&2
  exit 1
fi
env_tmp=""
cleanup() {
  if [[ -n "$env_tmp" ]]; then rm -f -- "$env_tmp"; fi
  rmdir "$lock_dir"
}
trap cleanup EXIT

seed="$(printf '%s' "$repo_root" | cksum | awk '{print $1}')"
port=$((18000 + seed % 10000))
attempts=0
while :; do
  reserved=false
  while IFS= read -r entry; do
    [[ "$entry" == worktree\ * ]] || continue
    other_root="${entry#worktree }"
    if [[ -f "$other_root/.env.local" ]] && \
       grep -Eq "^ARABICA__PORT=[\"']?${port}[\"']?$" "$other_root/.env.local"; then
      reserved=true
      break
    fi
  done < <(git -c core.quotePath=false worktree list --porcelain)
  if [[ "$reserved" == false ]] && ! lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
    break
  fi
  port=$((18000 + (port - 18000 + 1) % 10000))
  attempts=$((attempts + 1))
  if ((attempts >= 10000)); then
    echo "No free development port in 18000..27999." >&2
    exit 1
  fi
done

umask 077
dev_home="$repo_root/.local/dev-home"
mkdir -p "$dev_home"
# Copy configuration only, never sessions, databases, or saved authentication.
# Relative resource paths in the copied config should be reviewed locally.
if [[ ! -e "$dev_home/config.toml" && -f "$HOME/.arabica/config.toml" ]]; then
  cp "$HOME/.arabica/config.toml" "$dev_home/config.toml"
  chmod 600 "$dev_home/config.toml"
fi
dotenv_path() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\\\$}"
  printf '"%s"' "$value"
}
env_tmp="$(mktemp .env.local.XXXXXX)"
{
  printf '# Generated for this worktree; do not commit.\n'
  printf 'ARABICA_HOME=%s\n' "$(dotenv_path "$dev_home")"
  printf 'ARABICA__PORT=%s\n' "$port"
  printf 'ARABICA__TOOL_ROOT=%s\n' "$(dotenv_path "$repo_root")"
  printf 'ARABICA__ARCHIVE_ROOT=%s\n' "$(dotenv_path "$dev_home/archives")"
  printf 'ARABICA__EVALUATION_HOME=%s\n' "$(dotenv_path "$dev_home")"
  printf 'CARGO_BUILD_JOBS=4\n'
} > "$env_tmp"
mv "$env_tmp" .env.local
env_tmp=""
echo "Initialized isolated data at $dev_home and HTTP port $port."
echo "Review .envrc, then run direnv allow. Add HTTP provider settings from .env.example if needed."
