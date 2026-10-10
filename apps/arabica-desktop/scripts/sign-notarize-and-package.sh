#!/usr/bin/env bash
set -euo pipefail

arch="${1:?usage: sign-notarize-and-package.sh <aarch64|x86_64>}"
if [[ "$arch" != "aarch64" && "$arch" != "x86_64" ]]; then
  echo "unsupported desktop architecture: $arch" >&2
  exit 2
fi

required_secrets=(
  APPLE_DEVELOPER_ID_CERTIFICATE_P12_BASE64
  APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD
  APPLE_TEAM_ID
  APPLE_NOTARY_KEY_ID
  APPLE_NOTARY_ISSUER_ID
  APPLE_NOTARY_PRIVATE_KEY_BASE64
)
for name in "${required_secrets[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "missing required GitHub Actions secret: $name" >&2
    exit 1
  fi
done

app="dist/Arabica.app"
if [[ ! -d "$app" ]]; then
  echo "desktop app bundle not found: $app" >&2
  exit 1
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/arabica-sign.XXXXXX")"
keychain="$work/arabica-signing.keychain-db"
keychain_password="$(uuidgen)"
original_keychains=()
while IFS= read -r line; do
  original_keychains+=("$(printf '%s' "$line" \
    | sed -e 's/^[[:space:]]*//' -e 's/^"//' -e 's/"$//')")
done < <(security list-keychains -d user)
cleanup() {
  if [[ "${#original_keychains[@]}" -gt 0 ]]; then
    security list-keychains -d user -s "${original_keychains[@]}" >/dev/null 2>&1 || true
  fi
  security delete-keychain "$keychain" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

printf '%s' "$APPLE_DEVELOPER_ID_CERTIFICATE_P12_BASE64" | base64 -D > "$work/developer-id.p12"
printf '%s' "$APPLE_NOTARY_PRIVATE_KEY_BASE64" | base64 -D > "$work/notary-key.p8"

security create-keychain -p "$keychain_password" "$keychain"
security set-keychain-settings -lut 21600 "$keychain"
security unlock-keychain -p "$keychain_password" "$keychain"
security list-keychains -d user -s "$keychain" "${original_keychains[@]}"
security import "$work/developer-id.p12" \
  -k "$keychain" \
  -P "$APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD" \
  -T /usr/bin/codesign
security set-key-partition-list \
  -S apple-tool:,apple: \
  -s \
  -k "$keychain_password" \
  "$keychain" >/dev/null

identity="$(security find-identity -v -p codesigning "$keychain" \
  | awk -F '"' '/Developer ID Application:/ { print $2; exit }')"
if [[ -z "$identity" ]]; then
  echo "the imported certificate is not a valid Developer ID Application identity" >&2
  exit 1
fi

# Sign nested command-line code first, then the enclosing app bundle.
codesign --force --options runtime --timestamp --sign "$identity" \
  "$app/Contents/Resources/arabica"
codesign --force --options runtime --timestamp --sign "$identity" \
  --identifier dev.arabica.desktop \
  "$app/Contents/MacOS/ArabicaDesktop"
codesign --force --options runtime --timestamp --sign "$identity" "$app"
codesign --verify --deep --strict --verbose=2 "$app"

team_id="$(codesign -dv --verbose=4 "$app" 2>&1 \
  | sed -n 's/^TeamIdentifier=//p')"
if [[ "$team_id" != "$APPLE_TEAM_ID" ]]; then
  echo "signed app Team ID does not match APPLE_TEAM_ID" >&2
  exit 1
fi

archive="dist/Arabica-macos-${arch}.zip"
notary_archive="$work/Arabica-notarization.zip"
ditto -c -k --sequesterRsrc --keepParent "$app" "$notary_archive"
notary_result="$work/notary-result.json"
if ! xcrun notarytool submit "$notary_archive" \
    --key "$work/notary-key.p8" \
    --key-id "$APPLE_NOTARY_KEY_ID" \
    --issuer "$APPLE_NOTARY_ISSUER_ID" \
    --wait \
    --output-format json > "$notary_result"; then
  echo "Apple notarization submission failed; response follows:" >&2
  cat "$notary_result" >&2 || true
  exit 1
fi

submission_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id", ""))' "$notary_result")"
notary_status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status", "Unknown"))' "$notary_result")"
if [[ "$notary_status" != "Accepted" ]]; then
  echo "Apple notarization did not succeed (status: $notary_status); diagnostic log follows:" >&2
  if [[ -n "$submission_id" ]]; then
    xcrun notarytool log "$submission_id" "$work/notary-log.json" \
      --key "$work/notary-key.p8" \
      --key-id "$APPLE_NOTARY_KEY_ID" \
      --issuer "$APPLE_NOTARY_ISSUER_ID" || true
    cat "$work/notary-log.json" >&2 || true
  else
    cat "$notary_result" >&2
  fi
  exit 1
fi

xcrun stapler staple "$app"
xcrun stapler validate "$app"
codesign --verify --deep --strict --verbose=2 "$app"
spctl --assess --type execute --verbose=4 "$app"

ditto -c -k --sequesterRsrc --keepParent "$app" "$archive"
shasum -a 256 "$archive" > "$archive.sha256"
echo "Signed, notarized, and packaged $archive"
