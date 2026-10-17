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

## Legacy pytest compatibility testing

The `pytest-floor` CI job deliberately installs pytest 7.1.3 to check the documented compatibility floor. This version is affected by [CVE-2025-71176 / GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g), which concerns predictable temporary directories on Unix. Compatibility support does not mean that an old pytest version is recommended for normal use: use a current compatible pytest release containing the upstream fix (9.0.3 or later).

The legacy job runs on an ephemeral GitHub-hosted Ubuntu runner with read-only repository permissions and no persisted checkout credentials. Before running any tests, it creates a fresh, unpredictable directory with owner-only permissions (0700) under the runner's temporary directory. `PYTEST_DEBUG_TEMPROOT`, the standard temporary-directory variables, and `PYTEST_RUN_WITNESS_TEST_TMP` keep both pytest and the integration-test subprocesses inside that private tree. This follows the [upstream temporary-root workaround](https://github.com/pytest-dev/pytest/issues/13669) and restricts the advisory's attack path from a different local user. The directory is removed when the test step exits.

This is a mitigation for this CI job, not a patch to pytest 7.1.3 or protection for users' existing pytest installations. It does not isolate malicious code running as the same user, and its assumptions do not extend to shared self-hosted runners. Dependency scanners should continue to report the affected version; no advisory suppression is used. Remove this test-only exception when the historical compatibility floor is retired.
