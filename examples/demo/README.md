# Local proof demo

Install the package with its test extra, then run:

```console
python -m pip install -e ".[test]"
python scripts/demo.py --output examples/demo/run-local-01
```

Choose a new, empty output directory. The demo exercises a complete three-test run, a real child process that calls `os._exit(0)` after collection, missing/corrupt/stale receipts, a normal xdist run, and xdist worker loss. A second process verifies the generated receipts.

The script retains logs and receipt files in the chosen output directory. This demonstrates the package's completion-evidence contract; it does not qualify the hosting CI platform.
