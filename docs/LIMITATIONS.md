# Limitations and trust boundary

The library verifies one pytest invocation. Its denominator is exactly what
pytest collected before execution. It cannot see tests excluded by `-k`, `-m`,
`--ignore`, custom collection rules, shard configuration, or CI job selection.

The plugin runs in the pytest controller process. Test code and other plugins
share its environment and filesystem permissions. They can read the per-run
salt, mutate the journal, alter pytest hooks, or forge records. The receipt is
not signed, anti-tamper evidence, a sandbox, or a security attestation.

The parent wrapper catches ordinary child-process termination when it survives.
It cannot report its own abrupt death. A later `verify` step/job can detect a
missing or unfinished receipt if that verifier starts and receives the right
run ID. If both jobs disappear, or the CI authority reports success without
running the verifier, no code inside this library can repair the outer result.

A receipt that has every item terminal but lacks `SESSION_FINISHED` is still
incomplete. A receipt that contains `SESSION_FINISHED` but lacks the following
`DURABILITY_CONFIRMED` marker is also incomplete: that marker is written only
after the session record's file sync succeeds. A torn JSON line, missing
receipt, collection error, or unknown journal schema also fails closed.

The supported hooks are pytest's collection-finish and report hooks plus
pytest-xdist's worker-collection hook. Only the local Windows/Python/pytest
combinations listed in the evidence have been exercised. Third-party runners,
plugins that rewrite lifecycle hooks, and future pytest/xdist changes need
separate compatibility evidence.

A full CI workflow can still be incomplete while the one wrapped invocation is
complete. The library does not prove all jobs, matrices, shards, platforms, or
intended tests ran. It also does not prove a passing assertion is correct or
that the CI provider is trustworthy.

The wrapper has no timeout or process-tree supervisor. Normal CI timeouts and
cancellation behavior remain the caller's responsibility.
