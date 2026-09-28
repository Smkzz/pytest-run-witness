# Architecture

## Authority and lifetime

The wrapper creates a fresh journal before it starts `python -m pytest`. The
first line is `STARTED` and contains a caller-supplied or generated run ID. It
is written to a temporary file, flushed, file-synced, and atomically replaced
into the selected receipt path. This removes any prior receipt before pytest
starts. The implementation does not promise parent-directory syncing or
power-loss durability for the rename.

The explicitly loaded pytest plugin appends `COLLECTION_COMPLETE` before test
execution, then one `TEST_TERMINAL` event after each item receives its teardown
report. It appends `SESSION_FINISHED` with pytest's exit code and collection
error count and file-syncs that record. Only after that sync succeeds does it
append `DURABILITY_CONFIRMED`. The verifier requires this post-sync marker,
so a cache-visible `SESSION_FINISHED` whose sync failed cannot verify as
complete. Journal records are JSON Lines, written without a Python output
buffer. The collection and session records are synced; individual test events
are not synced one by one. A torn final record is rejected.

When the child process returns, the parent independently parses the entire
journal and compares the collected item set with terminal events. The wrapper
returns 0 only when pytest returned 0 and the journal is complete. A complete
test run that contains assertion failures keeps pytest's failure code. A gap
or incomplete record returns the dedicated wrapper code 10.

The separate command `pytest-run-witness verify RECEIPT --run-id ID` checks the
receipt without starting pytest. The expected run ID is required either as an
argument or `PYTEST_RUN_WITNESS_RUN_ID`; this prevents an old receipt from
being mistaken for the current job's evidence.

## Item identity and xdist

The denominator is the exact node IDs collected by this invocation. In direct
mode, the plugin reads `session.items`. Under xdist, the controller gathers
each worker collection through `pytest_xdist_node_collection_finished` and
requires the worker sequences to match. Only the controller writes the shared
journal; controller reports carry the test-phase outcomes from workers.

The wrapper generates a random per-run HMAC salt and gives it to the plugin
through the child environment. The receipt stores only a 128-bit keyed digest
for each node ID. It does not expose raw test names, parameter values, or file
paths. The verifier reports counts and cannot name missing items. The salt is
not in the journal, but target tests run in the same process and can read the
child environment. This is a privacy measure for the artifact, not a defense
against hostile tests.

## Item lifecycle and terminal state

An item is terminal only after pytest reports `when="teardown"`. Pytest's
normal protocol runs teardown after a passing, failing, or skipped setup and
after a passing, failing, skipped, xfailed, or xpassed call. Setup failure and
setup skip therefore reach terminal after their teardown report; teardown
failure still ends the item protocol. A call-phase report alone is not enough.

The in-memory state checks the expected phase order:

| Observed reports | Terminal? |
| --- | --- |
| setup passed → call result → teardown result | Yes, after teardown |
| setup failed or skipped → teardown result | Yes, after teardown |
| setup passed → no call or teardown | No |
| call result → no teardown | No |
| teardown failure | Yes; pytest result remains failed |

Rerun plugins may execute an item more than once. The first complete item
protocol counts once; an unfinished later retry still leaves the overall
session without a valid finish if the process is interrupted. A complete
receipt proves lifecycle completion, not test success or result quality.

## State and exit codes

| Journal result | Meaning | Wrapper result |
| --- | --- | --- |
| `COMPLETE` | Nonempty collection; every collected item reached teardown; session finish is present and its post-sync durability marker is present | Preserve pytest exit code |
| `GAP` | Session finished after an empty collection or without terminal events for all collected items | Wrapper exit 10 |
| `INCOMPLETE` | No session finish, invalid/corrupt/stale journal, collection error, invalid hook sequence, or xdist collection failure | Wrapper exit 10 |

The standalone verifier returns 0 only for `COMPLETE` and returns 11 for any
missing, invalid, incomplete, or wrong-run receipt. Any pytest test failure is
reported in the journal but does not turn a completeness check into a failure.
The test job's wrapper still preserves the ordinary pytest failure when the
receipt is complete.

Intentional selectors such as `-k`, `-m`, `--ignore`, and explicit node IDs
define the selected invocation and may be complete. `-x` and `--maxfail` stop
execution after collection; unstarted items make the invocation a `GAP`, even
when pytest itself already returns a test-failure code.

## Bounds and trust boundary

The implementation caps collected tests at 100,000, node-ID length at 16,384
characters, journal size at 32 MiB, and each line at 8 MiB. It rejects links or
reparse points as the receipt file, non-JSON events, duplicate JSON keys,
unknown fields/events, sequence gaps, duplicate identities, unexpected
terminal events, stale run IDs, and missing final newlines. The receipt contains
no filesystem paths, so it has no path traversal fields.

The plugin and tests run in the same user context. A malicious test or plugin
can read the salt and journal path, edit the receipt, monkeypatch hooks, or
forge events. This library is a correctness guard for trusted test code, not a
sandbox, security attestation, or defense against a compromised machine.

## Outermost CI boundary

If the test process or wrapper is killed before the journal's session-finish
event, the receipt is incomplete. A later verifier step/job can detect that if
it starts and receives the receipt. If the CI authority kills both jobs, never
schedules the verifier, or reports workflow success despite the verifier's
failure, this library cannot override that authority.

The local two-job example shows artifact and run-ID handoff. It is not a hosted
GitHub Actions result, test-matrix attestation, or proof that every CI job ran.
