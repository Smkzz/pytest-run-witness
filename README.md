# pytest-run-witness

**Fail closed when pytest stops before every collected test reaches a terminal outcome.**

`pytest-run-witness` wraps one pytest invocation, records the collected item set before execution, and writes a durable receipt as tests finish. A separate process can later verify that every collected item reached teardown.

A normal completed run looks like this:

```console
$ pytest-run-witness -- tests -q -n auto
RUN_ID=...
RECEIPT=.../.pytest-run-witness/receipt-....jsonl
...
VERIFIED 842/842
```

If execution stops early, the wrapper refuses to certify the run:

```text
INCOMPLETE 817/842; 25 collected tests have no terminal result (MISSING_TERMINAL_RESULTS)
```

## Install

```console
python -m pip install pytest-run-witness
```

Python 3.11–3.14 and pytest 7.1.3–9.x are supported by the 0.1.x release line. `pytest-xdist` is optional and is used only when your own pytest command uses it.

For development and the bundled xdist tests:

```console
python -m pip install -e ".[test]"
```

## Use it

Put your ordinary pytest arguments after `--`:

```console
pytest-run-witness -- tests -q
pytest-run-witness -- tests -q -n auto
pytest-run-witness -- -k smoke -m "not slow"
```

The wrapper launches `python -m pytest` with the same interpreter, working directory, environment, arguments, and configured plugins.

For a receipt that can be checked by a later CI step or job, choose a stable path and run ID:

```console
pytest-run-witness \
  --receipt .pytest-run-witness/receipt.jsonl \
  --run-id 123456-1 \
  -- tests -q -n auto

pytest-run-witness verify \
  .pytest-run-witness/receipt.jsonl \
  --run-id 123456-1
```

On GitHub Actions, a convenient run identity is `${{ github.run_id }}-${{ github.run_attempt }}`. See [examples/github-actions.yml](examples/github-actions.yml) for a two-job producer/verifier example.

## Exit behavior

`pytest-run-witness` does **not** turn ordinary test failures into success.

| Situation | Wrapper exit |
| --- | ---: |
| Pytest completed and every collected item reached a terminal outcome | pytest's own exit code |
| Completion cannot be proven | 10 |
| Wrapper usage error | 2 |

The independent verifier exits:

| Situation | Verifier exit |
| --- | ---: |
| Receipt proves complete execution for the expected run ID | 0 |
| Receipt is missing, corrupt, stale, wrong-run, or incomplete | 11 |

This distinction matters: a failing test suite can still be `VERIFIED n/n` because all selected tests finished. The wrapper then preserves pytest's nonzero exit code.

## What counts as terminal

An item is terminal after pytest reports its teardown phase. That includes:

- passed and failed calls;
- skips;
- xfail/xpass;
- setup failures and setup skips;
- teardown failures.

Selection flags still define the scope of the proof. A run using `-k`, `-m`, `--ignore`, a node ID, or another pytest selector can be fully verified for the subset pytest actually collected.

Options such as `-x` and `--maxfail` intentionally leave later collected tests unstarted. Those runs therefore produce an incomplete receipt.

## Why a separate verifier?

The journal starts before pytest runs and records collection before test execution. At normal session end it file-syncs the final session record and then writes a post-sync confirmation marker; verification fails closed if that marker is missing. The wrapper checks the receipt when the child process exits, but a later process can verify the same receipt independently:

```console
pytest-run-witness verify RECEIPT --run-id ID
```

That lets CI separate the test-producing job from the proof-checking job. The verifier still has to be scheduled by the outer CI system; this project cannot prove that the CI platform itself executed every intended job.

## Trust boundary

The guarantee is deliberately narrow:

- The denominator is the item set pytest collected for one invocation.
- It does not prove that pytest selected every test your repository intended.
- It does not prove that every CI matrix job or shard ran.
- The test process and pytest plugins share the same user context as the receipt and can modify it. This is not an anti-malware boundary.
- The receipt stores keyed item digests rather than node IDs or paths.
- A torn, malformed, stale, wrong-run, or incomplete journal fails closed.
- The wrapper does not manage external timeouts or guarantee process-tree termination.

See [Architecture](docs/ARCHITECTURE.md), [CLI contract](docs/CLI.md), [Limitations](docs/LIMITATIONS.md), and the [failure-mode matrix](docs/FAILURE_MODE_MATRIX.md).

## Development

```console
python -m pip install -e ".[test]"
python -m pytest
```

The hosted proof workflow covers Windows and Ubuntu, Python 3.11–3.14, a pytest 7.1.3 floor, current pytest 9.x, normal execution, abrupt process exit, missing/corrupt/stale receipts, and xdist worker loss.

## License

MIT.
