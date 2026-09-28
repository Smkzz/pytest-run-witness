# Local overhead benchmark

Raw samples, environment metadata, and the runtime source hash manifest:
[`benchmark-20260927-1905.json`](../evidence/benchmark-20260927-1905.json) and
[`benchmark-runtime-source-manifest.txt`](../evidence/benchmark-runtime-source-manifest.txt).

The run generated 10, 100, 1,000, and 10,000 passing items. It measured ten
direct and ten wrapped invocations per size in sequential and xdist (`-n 2`)
modes. The environment was CPython 3.11.16, pytest 7.1.3, xdist 3.6.1, on
Linux 6.18.33.2 WSL2 through `python:3.11-slim`. Sixteen logical CPUs were
visible; the container had no per-container cgroup CPU or memory quota. The
full per-run elapsed and sampled CPU data are in the JSON file.

| Items | Mode | Median direct / wrapped; delta | p95 direct / wrapped; delta | Receipt median; bytes/item |
| ---: | --- | --- | --- | --- |
| 10 | Sequential | 0.183 / 0.440 s; +0.257 s (+140.8%) | 0.209 / 0.479 s; +0.269 s (+128.7%) | 1,393 B; 139.3 |
| 10 | xdist | 0.478 / 0.697 s; +0.219 s (+45.7%) | 0.600 / 0.803 s; +0.203 s (+33.7%) | 1,388 B; 138.8 |
| 100 | Sequential | 0.229 / 0.494 s; +0.264 s (+115.4%) | 0.281 / 0.520 s; +0.239 s (+84.9%) | 11,658 B; 116.6 |
| 100 | xdist | 0.489 / 0.730 s; +0.241 s (+49.4%) | 0.520 / 1.078 s; +0.557 s (+107.1%) | 11,653 B; 116.5 |
| 1,000 | Sequential | 0.552 / 0.840 s; +0.288 s (+52.2%) | 0.585 / 0.899 s; +0.314 s (+53.8%) | 115,163 B; 115.2 |
| 1,000 | xdist | 0.828 / 1.065 s; +0.237 s (+28.7%) | 0.904 / 1.184 s; +0.280 s (+31.0%) | 115,158 B; 115.2 |
| 10,000 | Sequential | 4.205 / 4.756 s; +0.551 s (+13.1%) | 4.541 / 5.101 s; +0.560 s (+12.3%) | 1,159,168 B; 115.9 |
| 10,000 | xdist | 4.376 / 4.961 s; +0.585 s (+13.4%) | 4.697 / 5.446 s; +0.749 s (+15.9%) | 1,159,163 B; 115.9 |

Each p95 uses nearest-rank over ten samples, so it is the slowest sample in
that arm, not a stable population estimate. Percent overhead is the absolute
wrapped-minus-direct delta divided by the direct value. These synthetic suites
measure one mechanism under this local environment; they do not predict the
overhead of a user's tests or hosted runner.

| Collected items | Receipt records and logical record writes | Atomic replace calls | File fsync calls |
| ---: | ---: | ---: | ---: |
| 10 | 13 | 1 | 3 |
| 100 | 103 | 1 | 3 |
| 1,000 | 1,003 | 1 | 3 |
| 10,000 | 10,003 | 1 | 3 |

The write, replace, and fsync counts are source-level operations for a
successful complete run: one `STARTED` file write, one collection record, one
record per item, one session-finished record, one atomic file replacement,
and fsync of the start, collection, and finish records. They are not a syscall
trace; a partial `os.write` can require more than one syscall. The receipt
grows by about 115 bytes per item for the 10,000-item fixture.