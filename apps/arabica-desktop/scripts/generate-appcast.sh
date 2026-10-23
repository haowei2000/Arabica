#!/usr/bin/env bash
# Generate a signed architecture-specific Sparkle feed after notarization.
set -euo pipefail
arch="${1:?Expected architecture}"
tag="${2:?Expected release tag}"
case "$arch" in aarch64|x86_64) ;; *) exit 2 ;; esac
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Expected stable version tag' >&2; exit 2; }
: "${ARABICA_UPDATE_PRIVATE_KEY:?Update signing key is required}"
app_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$app_root"
tools="$app_root/.build/artifacts/sparkle/Sparkle/bin"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
archive="Arabica-macos-${arch}.zip"
cp "dist/$archive" "$work/$archive"
# Sparkle signs the feed as well as its enclosure because the app requires it.
printf '%s\n' "$ARABICA_UPDATE_PRIVATE_KEY" | "$tools/generate_appcast" \
  --ed-key-file - --maximum-deltas 0 \
  --download-url-prefix "https://github.com/haowei2000/Arabica/releases/download/$tag/" \
  --full-release-notes-url "https://github.com/haowei2000/Arabica/releases/tag/$tag" \
  -o "$app_root/dist/appcast-${arch}.xml" "$work"
