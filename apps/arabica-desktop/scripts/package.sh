#!/usr/bin/env bash
set -euo pipefail

profile="${1:-release}"
app_version="${ARABICA_VERSION:-0.1.0}"
app_version="${app_version#v}"
app_version="${app_version%%-*}"
app_version="${app_version%%+*}"
if [[ "$profile" != "debug" && "$profile" != "release" ]]; then
  echo "usage: $0 [debug|release]" >&2
  exit 2
fi

app_root="$(cd "$(dirname "$0")/.." && pwd)"
repo_root="$(cd "$app_root/../.." && pwd)"
swift_args=(-c "$profile")
cargo_args=(-p arabica-cli)
if [[ "$profile" == "release" ]]; then cargo_args+=(--release); fi

swift build --package-path "$app_root" "${swift_args[@]}"
cargo build --manifest-path "$repo_root/Cargo.toml" "${cargo_args[@]}"

swift_bin="$(swift build --package-path "$app_root" --show-bin-path "${swift_args[@]}")/ArabicaDesktop"
agent_bin="$repo_root/target/$profile/arabica"
bundle="$app_root/dist/Arabica.app"
rm -rf "$bundle"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"
cp "$swift_bin" "$bundle/Contents/MacOS/ArabicaDesktop"
cp "$agent_bin" "$bundle/Contents/Resources/arabica"
chmod 755 "$bundle/Contents/MacOS/ArabicaDesktop" "$bundle/Contents/Resources/arabica"

icon_work="$(mktemp -d)"
trap 'rm -rf "$icon_work"' EXIT
mkdir -p "$icon_work/Arabica.iconset"
if command -v rsvg-convert >/dev/null; then
  rsvg-convert -w 1024 -h 1024 "$repo_root/static/arabica-icon.svg" -o "$icon_work/source.png"
elif command -v resvg >/dev/null; then
  resvg -w 1024 -h 1024 "$repo_root/static/arabica-icon.svg" "$icon_work/source.png"
else
  echo "Install rsvg-convert or resvg to render static/arabica-icon.svg" >&2
  exit 1
fi
cp "$icon_work/source.png" "$bundle/Contents/Resources/arabica-icon.png"
if [ -d "$repo_root/apps/arabica-desktop/Sources/ArabicaDesktop/Resources/tool-icons" ]; then
  cp -R "$repo_root/apps/arabica-desktop/Sources/ArabicaDesktop/Resources/tool-icons" "$bundle/Contents/Resources/"
fi
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$icon_work/source.png" \
    --out "$icon_work/Arabica.iconset/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$icon_work/source.png" \
    --out "$icon_work/Arabica.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$icon_work/Arabica.iconset" -o "$bundle/Contents/Resources/Arabica.icns"

cat > "$bundle/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>ArabicaDesktop</string>
  <key>CFBundleIdentifier</key><string>dev.arabica.desktop</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Arabica</string>
  <key>CFBundleDisplayName</key><string>Arabica</string>
  <key>CFBundleIconFile</key><string>Arabica</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$app_version</string>
  <key>CFBundleVersion</key><string>$app_version</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

echo "Built $bundle"
