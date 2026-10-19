#!/usr/bin/env bash
set -euo pipefail

profile="${1:-release}"
if [[ "$profile" != "debug" && "$profile" != "release" ]]; then
  echo "usage: $0 [debug|release]" >&2
  exit 2
fi

app_root="${ARABICA_APP_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
app_root="$(cd "$app_root" && pwd)"
repo_root="$(cd "$app_root/../.." && pwd)"
app_version="${ARABICA_VERSION:-$(awk '/^\[workspace.package\]/ { package=1; next } package && /^version = / { gsub(/"/, "", $3); print $3; exit }' "$repo_root/Cargo.toml")}"
app_version="${app_version#v}"
app_version="${app_version%%-*}"
app_version="${app_version%%+*}"
update_public_key="$(cat "$repo_root/crates/arabica-cli/src/update-public-key.txt")"
case "${ARABICA_ARCH:-$(uname -m)}" in
  arm64|aarch64) update_arch=aarch64 ;;
  x86_64) update_arch=x86_64 ;;
  *) echo 'Unsupported update architecture' >&2; exit 1 ;;
esac
swift_args=(-c "$profile")
cargo_args=(-p arabica-cli)
if [[ "$profile" == "release" ]]; then cargo_args+=(--release); fi
agent_bin="$repo_root/target/$profile/arabica"
case "$(uname -m)" in arm64|aarch64) host_arch=aarch64 ;; x86_64) host_arch=x86_64 ;; esac
if [[ "$update_arch" != "$host_arch" ]]; then
  rust_target="${update_arch}-apple-darwin"
  swift_arch="$update_arch"
  if [[ "$swift_arch" == "aarch64" ]]; then swift_arch=arm64; fi
  swift_args+=(--triple "${swift_arch}-apple-macosx14.0")
  cargo_args+=(--target "$rust_target")
  agent_bin="$repo_root/target/$rust_target/$profile/arabica"
fi

swift build --package-path "$app_root" "${swift_args[@]}"
cargo build --manifest-path "$repo_root/Cargo.toml" "${cargo_args[@]}"

swift_bin="$(swift build --package-path "$app_root" --show-bin-path "${swift_args[@]}")/ArabicaDesktop"
bundle="$app_root/dist/Arabica.app"
rm -rf "$bundle"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"
cp "$swift_bin" "$bundle/Contents/MacOS/ArabicaDesktop"
cp "$agent_bin" "$bundle/Contents/Resources/arabica"
chmod 755 "$bundle/Contents/MacOS/ArabicaDesktop" "$bundle/Contents/Resources/arabica"
mkdir -p "$bundle/Contents/Frameworks"
swift_bin_dir="$(dirname "$swift_bin")"
test -d "$swift_bin_dir/Sparkle.framework"
ditto "$swift_bin_dir/Sparkle.framework" "$bundle/Contents/Frameworks/Sparkle.framework"
cp "$app_root/.build/artifacts/sparkle/Sparkle/LICENSE" "$bundle/Contents/Resources/Sparkle-LICENSE.txt"
install_name_tool -add_rpath '@executable_path/../Frameworks' "$bundle/Contents/MacOS/ArabicaDesktop"

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
  <key>SUFeedURL</key><string>https://github.com/haowei2000/Arabica/releases/latest/download/appcast-${update_arch}.xml</string>
  <key>SUPublicEDKey</key><string>$update_public_key</string>
  <key>SUEnableAutomaticChecks</key><true/>
  <key>SUAutomaticallyUpdate</key><true/>
  <key>SUScheduledCheckInterval</key><integer>86400</integer>
  <key>SUVerifyUpdateBeforeExtraction</key><true/>
  <key>SURequireSignedFeed</key><true/>
</dict></plist>
PLIST

if [[ "$profile" == "debug" ]]; then
  # install_name_tool invalidates Swift's linker-generated ad-hoc signature.
  codesign --force --sign - "$bundle/Contents/MacOS/ArabicaDesktop"
  codesign --force --sign - "$bundle"
fi

echo "Built $bundle"
