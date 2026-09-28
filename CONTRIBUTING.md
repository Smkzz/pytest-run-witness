# Contributing

Bug reports, focused reproductions, and compatibility reports are welcome.

## Development setup

```console
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[test]"   # Windows
# or: .venv/bin/python -m pip install -e ".[test]"
python -m pytest
```

The test suite intentionally includes abrupt-process and xdist worker-loss cases. Use the existing tests as the specification for exit codes and receipt semantics.

## Pull requests

Keep changes narrow and include tests for behavior changes. Changes to the receipt format, verifier rules, process handling, or GitHub Actions workflow should be treated as trust-sensitive and reviewed carefully.

Before opening a pull request, run:

```console
python -m pytest
python -m build
python -m twine check dist/*
```

Please avoid committing generated environments, build outputs, receipts, or local evidence directories.
