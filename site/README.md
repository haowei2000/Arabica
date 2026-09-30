# Arabica website

This is a static project site for GitHub Pages. The workflow in
`.github/workflows/website.yml` deploys this directory when it changes on
`main`. The site uses relative asset paths so it works at the repository's
`/Arabica/` Pages URL. It does not add a frontend to the Rust runtime.

`install.sh` downloads the current GitHub release for macOS or Linux, verifies
its SHA-256 sidecar, and installs the `arabica` binary to a user directory.
`test-install.sh` checks the archive and checksum flow with an offline fixture;
the Pages workflow runs it before publishing.
The homepage also links to the latest Arabica Desktop archives for Apple
silicon and Intel Macs. Tag releases build and attach both `.app` bundles with
SHA-256 sidecars; these builds are currently unsigned and not notarized.
`configuration.html` documents the settings implemented by the CLI and server.

The Source Serif 4 and Source Sans 3 webfonts are from the local Cheers site.
Both are licensed under the SIL Open Font License 1.1; their license texts are
included beside the font files in `assets/`.

To preview locally, run `python3 -m http.server 8765 --directory site` from the
repository root and open `http://localhost:8765/`.
