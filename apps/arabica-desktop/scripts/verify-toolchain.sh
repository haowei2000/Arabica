#!/usr/bin/env bash
set -euo pipefail

# Pin the released SDK explicitly; a runner image's default Xcode can lag
# behind its installed versions or move to a prerelease independently.
expected_xcode=27.1
expected_build=27A9275
expected_sdk=27.0
expected_minimum=14.0

mode="${1:-toolchain}"
case "$mode" in
  toolchain)
    if [[ "$#" -gt 1 ]]; then
      echo 'usage: verify-toolchain.sh [toolchain | bundle <app> <aarch64|arm64|x86_64>]' >&2
      exit 2
    fi
    ;;
  bundle)
    if [[ "$#" -ne 3 ]]; then
      echo 'usage: verify-toolchain.sh bundle <app> <aarch64|arm64|x86_64>' >&2
      exit 2
    fi
    ;;
  *)
    echo 'usage: verify-toolchain.sh [toolchain | bundle <app> <aarch64|arm64|x86_64>]' >&2
    exit 2
    ;;
esac

xcode_info="$(xcodebuild -version)"
sdk_version="$(xcrun --sdk macosx --show-sdk-version)"
swift_path="$(xcrun --find swift)"
echo "$xcode_info"
printf 'macOS SDK: %s\nSwift executable: %s\n' "$sdk_version" "$swift_path"
printf 'Host architecture: %s\n' "$(uname -m)"

xcode_version="$(awk '$1 == "Xcode" { print $2 }' <<< "$xcode_info")"
build_version="$(awk '$1 == "Build" && $2 == "version" { print $3 }' <<< "$xcode_info")"
if [[ "$xcode_version" != "$expected_xcode" || "$build_version" != "$expected_build" || "$sdk_version" != "$expected_sdk" ]]; then
  echo "Expected Xcode $expected_xcode ($expected_build) with macOS SDK $expected_sdk." >&2
  echo 'Select /Applications/Xcode_27.1.app/Contents/Developer using DEVELOPER_DIR.' >&2
  exit 1
fi

if [[ "$mode" == toolchain ]]; then exit 0; fi

app="$2"
case "$3" in
  aarch64|arm64) expected_arch=arm64 ;;
  x86_64) expected_arch=x86_64 ;;
  *) echo 'Unsupported desktop architecture.' >&2; exit 2 ;;
esac
desktop="$app/Contents/MacOS/ArabicaDesktop"
agent="$app/Contents/Resources/arabica"
for binary in "$desktop" "$agent"; do
  actual_arch="$(lipo -archs "$binary")"
  printf 'Executable architecture: %s (%s)\n' "$actual_arch" "$binary"
  if [[ "$actual_arch" != "$expected_arch" ]]; then
    echo "Expected the $expected_arch desktop and bundled CLI executables." >&2
    exit 1
  fi
done

# Inspect the linked binary rather than trusting the build machine's selected
# SDK. A new SDK does not require raising the minimum supported macOS version.
build_info="$(xcrun vtool -show-build "$desktop")"
echo "$build_info"
linked_sdk="$(awk '$1 == "sdk" { print $2 }' <<< "$build_info")"
minimum="$(awk '$1 == "minos" { print $2 }' <<< "$build_info")"
platform="$(awk '$1 == "platform" { print $2 }' <<< "$build_info")"
plist_minimum="$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$app/Contents/Info.plist")"
if [[ "$platform" != MACOS || "$linked_sdk" != "$expected_sdk" || "$minimum" != "$expected_minimum" || "$plist_minimum" != "$expected_minimum" ]]; then
  echo "Expected a macOS desktop linked with SDK $expected_sdk and deployment target $expected_minimum." >&2
  exit 1
fi
