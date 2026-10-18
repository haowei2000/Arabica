# Software updates

## Standalone CLI

Official standalone CLI installations check for stable releases in a separate
background process at most once every 24 hours when starting an interactive
terminal session. Updates replace the executable atomically; the current session
continues on its existing executable and new sessions use the installed version.
ACP, one-shot commands, redirected input/output, CI, and development builds do
not launch an automatic updater.

```bash
arabica --version
arabica update                 # Install the latest stable release now.
arabica update --check         # Check without installing.
arabica update --check --json  # Machine-readable status.
arabica update --auto off      # Disable automatic CLI updates.
arabica update --auto on       # Enable automatic CLI updates.
```

Set `ARABICA_NO_UPDATE=1` to suppress automatic checks for a process. This does
not block an explicit `arabica update`. Preferences, check timestamps, and the
last background failure are stored in `$ARABICA_HOME/updates.json`.

Cargo, Homebrew, Nix, and system-managed installations should be updated with
their package manager; the updater does not replace them. The CLI inside an
Arabica.app bundle is updated with the desktop app. `--check` is still available
for these installations.

The updater downloads only official assets for the running binary's platform.
Before extraction it verifies an Ed25519-signed manifest against the embedded
public key. That manifest binds the version, archive name, size, and SHA-256
digest. Extraction copies only the expected regular executable into a temporary
directory; other archive entries are never unpacked. Signature, checksum,
network, permission, or extraction failures leave the installed executable
unchanged. There is no automatic elevation or downgrade.

## macOS app

Release builds use Sparkle for automatic update checks, background downloads,
installation on quit, and relaunch. Use **Arabica → Check for Updates…** for a
manual check, or **Settings → Updates** to change automatic checks and downloads.
An update-triggered relaunch waits for active runs in all sessions to finish.
Debug builds and `swift run` do not start an updater.

Each architecture uses its own HTTPS appcast at:

- `https://github.com/haowei2000/Arabica/releases/latest/download/appcast-aarch64.xml`
- `https://github.com/haowei2000/Arabica/releases/latest/download/appcast-x86_64.xml`

The feed and update archive require Ed25519 signatures. The app also remains
Developer ID signed, notarized, and stapled. Sparkle updates the entire bundle,
including its ACP executable and framework helpers.

Versions shipped before this feature, including v0.6.0, require one manual
upgrade to a release that includes the updater. Subsequent releases can update
automatically.

## Release maintenance

The public key is tracked in
`crates/arabica-cli/src/update-public-key.txt` and embedded in both products.
The private key must never be committed or printed. The Arabica signing key is
stored in the maintainer's macOS Keychain under the Sparkle account
`dev.arabica.updates`; CI reads the exported key from the repository Actions
secret `ARABICA_UPDATE_PRIVATE_KEY`.

The tag release workflow:

1. Builds the five supported CLI archives and both desktop architectures.
2. Signs all nested desktop helpers, notarizes each app, and generates its
   signed architecture-specific appcast.
3. Generates a `.update.json` manifest and detached `.sig` for every CLI archive,
   verifying the signing key matches the tracked public key.
4. Publishes releases only after signatures and both desktop builds succeed.

`scripts/sign-cli-updates.mjs` uses the pinned Sparkle `sign_update` tool and
verifies signatures with Node's Ed25519 implementation. Desktop feeds are
generated with Sparkle's `generate_appcast`. Both signing tools read the private
key through standard input. Local development builds do not require a private
key. Keep the public key stable across releases; key rotation requires a
separate compatibility and recovery plan.

For a fork, generate a separate signing key, replace the public key, configure
the Actions secret, and change the repository/feed URLs before distributing it.
