#!/bin/sh
# Install the latest Arabica release on macOS or Linux.
set -eu

fail() {
  printf 'arabica install: %s\n' "$1" >&2
  exit 1
}

for tool in curl tar install mktemp uname; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is required"
done

case "$(uname -s)" in
  Darwin) platform=macos; target_os=apple-darwin ;;
  Linux) platform=linux; target_os=unknown-linux-gnu ;;
  *) fail 'only macOS and Linux are supported; use GitHub Releases for Windows' ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=x86_64 ;;
  arm64|aarch64) arch=aarch64 ;;
  *) fail 'unsupported CPU architecture; check GitHub Releases' ;;
esac

archive="structure-${platform}-${arch}.tar.gz"
target="${arch}-${target_os}"
version="${ARABICA_VERSION:-latest}"
case "$version" in
  latest) release_url="https://github.com/haowei2000/Arabica/releases/latest/download" ;;
  v[0-9]*)
    case "$version" in
      *[!a-zA-Z0-9._-]*) fail 'ARABICA_VERSION contains an invalid character' ;;
    esac
    release_url="https://github.com/haowei2000/Arabica/releases/download/${version}" ;;
  *) fail 'ARABICA_VERSION must be a release tag such as v0.1.1' ;;
esac
install_dir="${ARABICA_INSTALL_DIR:-${HOME:?HOME is required}/.local/bin}"
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/arabica-install.XXXXXX")"
trap 'rm -rf "$temp_dir"' 0 1 2 15

curl -fL --retry 3 --silent --show-error "${release_url}/${archive}" -o "${temp_dir}/${archive}"
curl -fL --retry 3 --silent --show-error "${release_url}/${archive}.sha256" -o "${temp_dir}/${archive}.sha256"
(
  cd "$temp_dir"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -c "${archive}.sha256"
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c "${archive}.sha256"
  else
    fail 'shasum or sha256sum is required to verify the download'
  fi
)

tar -xzf "${temp_dir}/${archive}" -C "$temp_dir"
[ -f "${temp_dir}/structure-${target}/arabica" ] || fail 'release archive has no arabica binary'
mkdir -p "$install_dir"
install -m 755 "${temp_dir}/structure-${target}/arabica" "${install_dir}/arabica"
printf 'Installed arabica to %s/arabica\n' "$install_dir"
case ":${PATH}:" in
  *":${install_dir}:"*) ;;
  *) printf 'Add %s to PATH to run arabica from any directory.\n' "$install_dir" ;;
esac
