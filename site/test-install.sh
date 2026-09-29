#!/bin/sh
# Offline conformance test for the release installer's archive contract.
set -eu
root_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/arabica-install-test.XXXXXX")"
trap 'rm -rf "$test_dir"' 0 1 2 15
mkdir -p "$test_dir/fixtures/structure-aarch64-apple-darwin" "$test_dir/shims" "$test_dir/destination"
cat > "$test_dir/fixtures/structure-aarch64-apple-darwin/arabica" <<'BINARY'
#!/bin/sh
printf 'fixture arabica\n'
BINARY
chmod +x "$test_dir/fixtures/structure-aarch64-apple-darwin/arabica"
archive=structure-macos-aarch64.tar.gz
tar -czf "$test_dir/fixtures/$archive" -C "$test_dir/fixtures" structure-aarch64-apple-darwin
(
  cd "$test_dir/fixtures"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$archive" > "$archive.sha256"
  else
    sha256sum "$archive" > "$archive.sha256"
  fi
)
cat > "$test_dir/shims/uname" <<'UNAME'
#!/bin/sh
case "$1" in
  -s) printf 'Darwin\n' ;;
  -m) printf 'arm64\n' ;;
  *) exit 1 ;;
esac
UNAME
cat > "$test_dir/shims/curl" <<'CURL'
#!/bin/sh
set -eu
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) destination=$2; shift 2 ;;
    --retry) shift 2 ;;
    -*) shift ;;
    *) url=$1; shift ;;
  esac
done
case "$url" in
  https://github.com/haowei2000/Arabica/releases/download/v0.1.1/*) ;;
  *) exit 1 ;;
esac
cp "$ARABICA_INSTALL_TEST_FIXTURES/${url##*/}" "$destination"
CURL
chmod +x "$test_dir/shims/uname" "$test_dir/shims/curl"
ARABICA_INSTALL_TEST_FIXTURES="$test_dir/fixtures" \
ARABICA_INSTALL_DIR="$test_dir/destination" \
ARABICA_VERSION=v0.1.1 \
PATH="$test_dir/shims:$PATH" \
sh "$root_dir/install.sh"
[ "$("$test_dir/destination/arabica")" = 'fixture arabica' ]
printf 'Offline installer conformance test passed\n'
