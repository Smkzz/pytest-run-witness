# Two-job verifier example

The pinned example is [`../github-actions.yml`](../github-actions.yml).

It uploads the receipt with `if: always()`, schedules the verifier job with `if: always()`, passes the expected run/attempt identity, and treats the uploaded receipt as the verifier's input.

The repository CI workflow also exercises the same producer/verifier boundary on hosted runners. The example itself is illustrative; a consuming project still needs to decide how long to retain receipts and how to surface verifier failures.
