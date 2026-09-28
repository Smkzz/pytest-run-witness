# CLI and receipt contract

## Commands

Run one pytest invocation through the wrapper:

```console
pytest-run-witness [--receipt PATH] [--run-id ID] -- [pytest arguments ...]
```

Options before `--` belong to the wrapper. Everything after `--` is passed to
pytest. If `--receipt` is omitted, the wrapper creates
`.pytest-run-witness/receipt-<random-id>.jsonl` under the current working
directory. The path is printed as `RECEIPT=...`. A supplied receipt path is
initialized for this run before pytest starts, replacing any earlier receipt
at that path. The wrapper generates a run ID unless one is supplied with
`--run-id`; it prints the value as `RUN_ID=...`.

Verify a receipt independently:

```console
pytest-run-witness verify RECEIPT [--run-id ID]
```

Verification requires the expected run identity through `--run-id` or the
`PYTEST_RUN_WITNESS_RUN_ID` environment variable. Supply the identity from the
current CI run so a receipt from an earlier run cannot satisfy the check.

## Exit codes

| Command outcome | Exit code |
| --- | ---: |
| Wrapper: pytest passed and the receipt is complete | `0` |
| Wrapper: receipt is complete and pytest returned nonzero | pytest's exact exit code |
| Wrapper: completion cannot be proved, including an incomplete journal | `10` |
| `verify`: receipt is complete for the expected run ID | `0` |
| `verify`: run ID is missing/invalid or receipt is missing, invalid, incomplete, or belongs to another run | `11` |
| Invalid command-line syntax or arguments | `2` |

The wrapper's completion-proof code is outside pytest's documented standard
exit-code range of `0` through `5`. A
complete pytest run that reports assertion failures keeps pytest's failure
code. When the receipt is incomplete, code `10` communicates that the wrapper
could not prove the invocation completed; it does not convert a complete
pytest failure into an infrastructure failure. The independent verifier only
checks completion and run identity. It returns `0` for a complete receipt even
when the receipt records a pytest test failure; the wrapper retains that
pytest result.

`-x` and `--maxfail` may stop after collection. If collected items never reach
teardown, the invocation is a gap and the wrapper returns `10`, even when
pytest also reports a test failure. The guarantee covers the items selected
for that invocation, not tests the project intended but pytest did not select.

## Receipt schema version 1

The receipt is UTF-8 JSON Lines. Each line is one JSON object. The first event
declares `schema_version: 1`; later events are interpreted under that version.
The verifier requires exact event fields, valid order, contiguous sequence
numbers, and a final newline. Unknown fields and event types are rejected.

| Event | Exact fields | Meaning |
| --- | --- | --- |
| `STARTED` | `schema_version` (integer `1`), `type`, `seq` (integer `0`), `run_id` (string) | File-synced run header; written before pytest starts. |
| `COLLECTION_COMPLETE` | `seq`, `type`, `collected_tests` (integer), `item_ids` (array of unique 32-character lowercase hex digests) | The exact nonempty set selected by pytest. |
| `TEST_TERMINAL` | `seq`, `type`, `item_id` (one collected digest) | One selected item reached its teardown report. |
| `SESSION_FINISHED` | `seq`, `type`, `pytest_exit_code` (integer), `collection_errors` (integer), `incomplete_reason` (string or `null`) | Pytest's session-finish record and collection status. |

`seq` is a zero-based, gap-free integer sequence across all events. Item IDs
are keyed digests of pytest node IDs; raw node IDs and file paths are not
written to the receipt. Schema version 1 caps the collection at 100,000 items
and the journal at 32 MiB.

The verifier reports `COMPLETE` only when the journal is structurally valid,
the expected run ID matches, the collection is nonempty, every collected item
has exactly one terminal event, and `SESSION_FINISHED` reports no collection
error or incomplete reason. Empty collections and missing terminal events are
`GAP`; missing finalization, malformed data, stale identities, and invariant
violations are `INCOMPLETE`. Both states fail closed.

This receipt is completion evidence for one declared pytest invocation. It
does not prove that all intended tests or CI matrix jobs were selected or run,
that the CI platform is trustworthy, or that test code could not alter the
receipt. If CI kills both the test and verifier jobs or suppresses the
verifier, the library cannot detect that outermost failure.
