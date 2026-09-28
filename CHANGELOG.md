# Changelog

All notable user-facing changes are documented here.

## 0.1.0 — 2026-09-28

Initial release.

### Added

- A pytest wrapper that records the collected item set before test execution.
- Durable per-run receipt journals with keyed item digests.
- Fail-closed detection when collected tests do not reach terminal teardown reports.
- Independent receipt verification in a separate process or CI job.
- Preservation of pytest's original exit code when completion is proven.
- Support for ordinary pytest selection arguments and pytest-xdist.
- Detection of missing, corrupt, stale, wrong-run, hard-linked, and reparse/symlink receipt cases.
- Hosted proof coverage across Windows and Ubuntu on Python 3.11–3.14.
- Fault-injection coverage for abrupt process exit and xdist worker loss.
