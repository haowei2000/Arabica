# Releasing the macOS app

Tag releases build the desktop app for Apple silicon and Intel Macs. The
release workflow signs both the bundled CLI and the app with a Developer ID
Application certificate, enables the hardened runtime, submits a ZIP to
Apple's notary service, staples the accepted ticket to the app, and publishes
the final ZIP with a SHA-256 sidecar.

Desktop builds use the `xcode-27` runner image and explicitly select Xcode 27.1
(27A9275), with the macOS 27.0 SDK. The runner image is currently in public
preview; the selected Xcode build is Apple's stable release. Both architectures
build on the ARM runner, using the packaging script's Intel cross-compilation
support. The minimum deployment target remains macOS 14.

The workflow verifies the toolchain before building and checks the desktop
Mach-O build version afterward. This prevents a runner's older default Xcode
from silently producing the previous system appearance. Local builds may use
an older supported toolchain, but visual review of the current native design
must use the pinned SDK and a current macOS release.

## GitHub Actions credentials

The repository needs these Actions secrets before pushing a release tag:

| Secret | Value |
| --- | --- |
| `APPLE_DEVELOPER_ID_CERTIFICATE_P12_BASE64` | Base64-encoded exported Developer ID Application `.p12` certificate |
| `APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD` | Password used when exporting the `.p12` |
| `APPLE_TEAM_ID` | Team ID shown in the Developer ID certificate |
| `APPLE_NOTARY_KEY_ID` | App Store Connect API key ID |
| `APPLE_NOTARY_ISSUER_ID` | App Store Connect API issuer ID |
| `APPLE_NOTARY_PRIVATE_KEY_BASE64` | Base64-encoded App Store Connect `.p8` private key |

Create the Developer ID Application certificate in the Apple Developer
account, export it with its private key as a password-protected `.p12`, and
create an App Store Connect API key for notarization. Add the values under
**GitHub repository → Settings → Secrets and variables → Actions**. Never
commit the `.p12`, `.p8`, or their passwords.

The workflow fails with the name of any missing secret rather than publishing
an unsigned desktop build. Local `package.sh` builds remain unsigned for
development.
