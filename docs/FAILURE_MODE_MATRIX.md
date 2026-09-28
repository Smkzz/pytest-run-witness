# Failure-mode matrix

Evidence in this matrix is from the local integration suite, run on Windows
with CPython 3.12.14 / pytest 9.1.1 / pytest-xdist 3.8.0 and on Linux with
CPython 3.11.16 / pytest 7.1.3 / pytest-xdist 3.6.1. The final local runs each
passed 46 tests. The Linux run copied the
source onto the container's Linux filesystem; the Windows-mounted test-temp
attempt is excluded because pytest's own capture-file cleanup failed there.

`pytest exit` is the inner child's result; `wrapper exit` is the command users
run. Standard pytest exits are `0` through `5`; wrapper incomplete is `10`, and
independent verifier rejection is `11`. The suite asserts these exact values.
For abrupt exits, pytest cannot always return normally, so the table records
the observable process result and the receipt state separately.

| Scenario | pytest exit | wrapper exit | Receipt | Collected / terminal | Classification | Detection |
| --- | ---: | ---: | --- | ---: | --- | --- |
| Three passing tests | 0 | 0 | Present, finished | 3 / 3 | COMPLETE | Pass |
| Call failure, skip, xfail/xpass, setup failure, teardown failure, and pass | 1 | 1 | Present, finished | 7 / 7 | COMPLETE; pytest failure preserved | Pass |
| `-k`, `-m`, `--ignore`, explicit node ID | 0 | 0 | Present, finished | 1/1, 1/1, 2/2, 1/1 | COMPLETE for pytest's selected collection | Pass |
| `-x` | 1 | 10 | Present, finished | 1 / 3 | GAP; unstarted selected items are missing | Pass |
| `--maxfail=1` | 1 | 10 | Present, finished | 1 / 3 | GAP; unstarted selected items are missing | Pass |
| Empty collection | 5 | 10 | Present, finished | 0 / 0 | GAP (`EMPTY_COLLECTION`) | Pass |
| Collection syntax error | 2 | 10 | Present, collection error | Not a valid denominator | INCOMPLETE | Pass |
| Real test calls `os._exit(0)` after three items collected | 0 in raw pytest control; 0 child outcome | 10 | Present, no finish | 3 / 0 | INCOMPLETE (`SESSION_NOT_FINISHED`) | Pass; independent verifier exits 11 |
| Real test calls `os._exit(23)` | 23 | 10 | Present, no finish | 2 / 0 | INCOMPLETE | Pass |
| Real `os._exit(0)` in `pytest_sessionstart` | 0 child outcome | 10 | Present, no finish | Collection not completed | INCOMPLETE | Pass; independent verifier exits 11 |
| Real `os._exit(0)` during collection | 0 child outcome | 10 | Present, no finish | Collection not completed | INCOMPLETE | Pass; independent verifier exits 11 |
| Pytest internal error during `pytest_sessionstart` | 3 in raw pytest control; 3 child outcome | 10 | Present, no finish | Collection not completed | INCOMPLETE | Pass; independent verifier exits 11 |
| `pytest.exit(returncode=0)` before remaining tests | 0 from pytest's exit path | 10 | Present | Items remain unreported | INCOMPLETE/GAP | Pass |
| SIGTERM or SIGINT reaches the pytest child | Interrupted/nonzero | 10 | Present, no valid finish | Tests remain unreported | INCOMPLETE | Pass on both tested OSes |
| Hook returns success before any test executes | 0 | 10 | Present, finished | 0 / 3 | GAP | Pass |
| All three teardown events written, then `os._exit(0)` before session finish | 0 child outcome | 10 | Present, no finish | 3 / 3 | INCOMPLETE | Pass; independent verifier exits 11 |
| Hook raises before collection is recorded | Nonzero | 10 | Present, invalid/incomplete lifecycle | No valid denominator | INCOMPLETE | Pass |
| xdist `load`, `loadscope`, `worksteal` | 0 | 0 | Present, finished | 6 / 6 each | COMPLETE | Pass |
| xdist `-n 2` and `-n auto` | 0 | 0 | Present, finished | 4 / 4 each | COMPLETE | Pass; auto worker count fixed to 2 in fixture |
| xdist worker calls `os._exit(0)` or `os._exit(41)` after receiving tests | Nonzero controller outcome | 10 | Present, invalid worker report | Partial worker reports | INCOMPLETE | Pass; green completion rejected |
| xdist worker killed externally after receiving a test | Nonzero controller outcome | 10 | Present | Partial worker reports | INCOMPLETE | Pass on both tested OSes |
| xdist controller exits 0 early | 0 child outcome | 10 | Present, no finish | Collection/terminal progress may be partial | INCOMPLETE | Pass |
| Wrapper dies while pytest is active | Wrapper has no exit result | N/A | Present, unfinished | 0 / 1 at independent check | INCOMPLETE | Linux process-group kill passed; Windows tree termination was denied by this host and the cleanup test skipped after the independent check had returned 11 |
| Missing, stale, wrong-run-ID, corrupted, torn, duplicate, or malformed journal | N/A | N/A | Missing/invalid | Not trusted | INCOMPLETE | Pass in verifier tests |

The unwrapped raw `os._exit(0)` control is deliberately not a receipt-bearing
run; it confirms that the same abrupt process exit can look green to plain
pytest. The wrapped command atomically initializes the receipt before starting
its child.

## Boundary

The parent detects child termination only while it survives. If the wrapper is
destroyed, only a later verifier can reject the missing or unfinished receipt.
That verifier can help only when the outer CI system schedules it and makes
the receipt available. The test process shares filesystem and environment
authority with the plugin, so this is not malicious-test resistance.

The #1106-shaped local cases terminate before collection, during collection,
and during execution with a zero child outcome. They test the failure shape;
they do not reproduce Kubernetes, ARC, or GitHub Actions Runner Controller.
