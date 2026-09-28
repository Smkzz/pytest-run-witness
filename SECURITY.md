# Security policy

## Supported versions

The 0.1.x release line receives security fixes while it is the latest release line.

## Reporting a vulnerability

Please use GitHub Security Advisories for vulnerabilities that could affect users of `pytest-run-witness`:

https://github.com/Smkzz/pytest-run-witness/security/advisories/new

Please do not open a public issue for a vulnerability before a coordinated fix is available.

## Security boundary

`pytest-run-witness` is a completion-evidence tool, not a sandbox.

The pytest process, its plugins, and tests normally run with the same user permissions as the receipt. A malicious test or plugin with those permissions can tamper with local files, including evidence. The verifier is designed to reject malformed, stale, incomplete, linked, and wrong-run receipt inputs, but it does not create a security boundary against code already executing as the same user.

The outer CI platform is also outside the proof boundary. If the platform does not schedule the verifier, withholds the expected artifact, or falsely reports job state, this package cannot correct that platform-level behavior.
