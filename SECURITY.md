# Security Policy

## Supported Versions

Security fixes are handled on the default branch. If release branches are added,
this policy should be updated with the supported version matrix.

## Reporting a Vulnerability

Please do not open a public issue for suspected vulnerabilities.

Use GitHub private vulnerability reporting if it is enabled for the repository,
or contact the maintainers through the private maintainer channel listed on the
repository profile. Include:

- Affected version or commit.
- Clear reproduction steps.
- Expected and actual impact.
- Any relevant logs, screenshots, or proof-of-concept details.

## Handling Expectations

Maintainers should acknowledge valid reports within 3 business days, keep the
reporter updated during triage, and publish a fix or mitigation once the issue is
understood.

## Scope

Security-sensitive areas include authentication, authorization, model and storage
credentials, Redis/PostgreSQL access, file uploads, tool execution, sandboxing,
deployment scripts, and generated artifacts that may contain user data.
