# Arabica website

This is a static project site for GitHub Pages. The workflow in
`.github/workflows/website.yml` deploys this directory when it changes on
`main`. The site uses relative asset paths so it works at the repository's
`/Arabica/` Pages URL. It does not add a frontend to the Rust runtime.

The Source Serif 4 and Source Sans 3 webfonts are from the local Cheers site.
Both are licensed under the SIL Open Font License 1.1; their license texts are
included beside the font files in `assets/`.

To preview locally, run `python3 -m http.server 8765 --directory site` from the
repository root and open `http://localhost:8765/`.
