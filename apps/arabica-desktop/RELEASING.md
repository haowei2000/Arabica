# Releasing the macOS app

Tag releases build the desktop app for Apple silicon and Intel Macs. The
release workflow signs both the bundled CLI and the app with a Developer ID
Application certificate, enables the hardened runtime, submits a ZIP to
Apple's notary service, staples the accepted ticket to the app, and publishes
the final ZIP with a SHA-256 sidecar.

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
