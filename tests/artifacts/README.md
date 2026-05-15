# Test Artifacts

This directory is for local and CI-generated test run artifacts.

Integration runs may write browser traces, screenshots, Docker logs, event
streams, transcripts, token usage, JUnit XML, and summary files under
`tests/artifacts/integration/`.

These files can be large and may include environment-specific logs or test
conversation content, so they are intentionally ignored by Git. CI should upload
this directory as a pipeline artifact when the results need to be retained.
