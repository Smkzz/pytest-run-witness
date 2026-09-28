from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path


def _process_cpu_tree(root_pid: int) -> dict[int, int]:
    """Return user+system CPU ticks for the root process and live descendants."""
    parents: dict[int, int] = {}
    ticks: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
            pid = int(entry.name)
            parents[pid] = int(fields[1])
            ticks[pid] = int(fields[11]) + int(fields[12])
        except (OSError, ValueError, IndexError):
            continue
    tree = {root_pid}
    while True:
        children = {pid for pid, parent in parents.items() if parent in tree}
        expanded = tree | children
        if expanded == tree:
            break
        tree = expanded
    return {pid: ticks[pid] for pid in tree if pid in ticks}


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _cgroup_limit(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if value == "max":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _run_one(
    *, root: Path, size: int, mode: str, wrapped: bool, repetition: int
) -> dict[str, object]:
    key = f"n{size}-{mode}-{'wrapped' if wrapped else 'direct'}-{repetition:02d}"
    case_root = root / key
    case_root.mkdir(parents=True)
    suite = case_root / "test_generated.py"
    suite.write_text(
        "".join(f"def test_case_{index:05d}(): pass\n" for index in range(size)),
        encoding="utf-8",
    )
    base_temp = case_root / "pytest-temp"
    child_temp = case_root / "child-temp"
    child_temp.mkdir()
    receipt = case_root / "receipt.jsonl"
    pytest_args = ["-q", "-p", "no:cacheprovider", "--basetemp", str(base_temp), str(suite)]
    if mode == "xdist":
        pytest_args[1:1] = ["-n", "2"]
    if wrapped:
        command = [
            sys.executable,
            "-m",
            "pytest_run_witness",
            "--receipt",
            str(receipt),
            "--run-id",
            key,
            "--",
            "pytest",
            *pytest_args,
        ]
    else:
        command = [sys.executable, "-m", "pytest", *pytest_args]

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path("src").resolve()) + os.pathsep + environment.get("PYTHONPATH", "")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["TEMP"] = str(child_temp)
    environment["TMP"] = str(child_temp)
    environment["TMPDIR"] = str(child_temp)

    start = time.perf_counter()
    process = subprocess.Popen(command, cwd=case_root, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    cpu_ticks: dict[int, int] = {}
    while process.poll() is None:
        for pid, used in _process_cpu_tree(process.pid).items():
            cpu_ticks[pid] = max(cpu_ticks.get(pid, 0), used)
        time.sleep(0.01)
    output, _ = process.communicate()
    for pid, used in _process_cpu_tree(process.pid).items():
        cpu_ticks[pid] = max(cpu_ticks.get(pid, 0), used)
    elapsed = time.perf_counter() - start
    receipt_bytes = receipt.stat().st_size if receipt.exists() else 0
    receipt_lines = len(receipt.read_bytes().splitlines()) if receipt.exists() else 0
    if process.returncode != 0:
        raise RuntimeError(f"{key} exited {process.returncode}:\n{output[-4000:]}")
    if wrapped and f"VERIFIED {size}/{size}" not in output:
        raise RuntimeError(f"{key} did not verify {size}/{size}:\n{output[-4000:]}")
    return {
        "case": key,
        "size": size,
        "mode": mode,
        "kind": "wrapped" if wrapped else "direct",
        "repetition": repetition,
        "exit_code": process.returncode,
        "elapsed_seconds": elapsed,
        "process_tree_cpu_seconds": sum(cpu_ticks.values()) / os.sysconf("SC_CLK_TCK"),
        "receipt_bytes": receipt_bytes,
        "receipt_record_count": receipt_lines,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    if not Path("/proc").is_dir():
        raise SystemExit("benchmark requires Linux /proc process accounting")
    if args.repeats < 10:
        raise SystemExit("use at least 10 repetitions for the reported p95")
    if args.output_root.exists() or args.output.exists():
        raise SystemExit("refusing to overwrite benchmark inputs or evidence")
    args.output_root.mkdir(parents=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    raw: list[dict[str, object]] = []
    for size in (10, 100, 1_000, 10_000):
        for mode in ("sequential", "xdist"):
            for wrapped in (False, True):
                for repetition in range(1, args.repeats + 1):
                    raw.append(
                        _run_one(
                            root=args.output_root,
                            size=size,
                            mode=mode,
                            wrapped=wrapped,
                            repetition=repetition,
                        )
                    )

    summaries: list[dict[str, object]] = []
    for size in (10, 100, 1_000, 10_000):
        for mode in ("sequential", "xdist"):
            group = [row for row in raw if row["size"] == size and row["mode"] == mode]
            direct = [row for row in group if row["kind"] == "direct"]
            wrapped = [row for row in group if row["kind"] == "wrapped"]
            for metric in ("elapsed_seconds", "process_tree_cpu_seconds"):
                direct_values = [float(row[metric]) for row in direct]
                wrapped_values = [float(row[metric]) for row in wrapped]
                summaries.append(
                    {
                        "size": size,
                        "mode": mode,
                        "metric": metric,
                        "direct_median": statistics.median(direct_values),
                        "wrapped_median": statistics.median(wrapped_values),
                        "median_delta": statistics.median(wrapped_values)
                        - statistics.median(direct_values),
                        "median_overhead_percent": (
                            100
                            * (
                                statistics.median(wrapped_values)
                                - statistics.median(direct_values)
                            )
                            / statistics.median(direct_values)
                            if statistics.median(direct_values)
                            else None
                        ),
                        "direct_p95_nearest_rank": _percentile(direct_values, 0.95),
                        "wrapped_p95_nearest_rank": _percentile(wrapped_values, 0.95),
                        "p95_delta": _percentile(wrapped_values, 0.95)
                        - _percentile(direct_values, 0.95),
                        "p95_overhead_percent": (
                            100
                            * (
                                _percentile(wrapped_values, 0.95)
                                - _percentile(direct_values, 0.95)
                            )
                            / _percentile(direct_values, 0.95)
                            if _percentile(direct_values, 0.95)
                            else None
                        ),
                        "samples_per_arm": args.repeats,
                    }
                )
            receipt_sizes = [int(row["receipt_bytes"]) for row in wrapped]
            record_counts = [int(row["receipt_record_count"]) for row in wrapped]
            summaries.append(
                {
                    "size": size,
                    "mode": mode,
                    "metric": "receipt_storage",
                    "median_bytes": statistics.median(receipt_sizes),
                    "p95_bytes_nearest_rank": _percentile(receipt_sizes, 0.95),
                    "median_bytes_per_item": statistics.median(receipt_sizes) / size,
                    "p95_bytes_per_item_nearest_rank": _percentile(receipt_sizes, 0.95) / size,
                    "receipt_records_min": min(record_counts),
                    "receipt_records_max": max(record_counts),
                    "receipt_record_write_operations": size + 3,
                    "atomic_replacement_calls": 1,
                    "file_fsync_calls": 3,
                    "io_count_basis": (
                        "source-level receipt operations for a complete run: one STARTED write, "
                        "one collection append, one append per item, one session-finish append; "
                        "one atomic replace; fsync STARTED, collection, and session-finish"
                    ),
                    "samples_per_arm": args.repeats,
                }
            )

    document = {
        "schema_version": 1,
        "environment": {
            "platform": platform.platform(),
            "logical_cpu_count": os.cpu_count(),
            "cgroup_cpu_quota": (
                Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").strip()
                if Path("/sys/fs/cgroup/cpu.max").is_file()
                else None
            ),
            "cgroup_memory_limit_bytes": _cgroup_limit(Path("/sys/fs/cgroup/memory.max")),
            "python": sys.version,
            "pytest": subprocess.check_output([sys.executable, "-m", "pytest", "--version"], text=True).strip(),
            "xdist": subprocess.check_output([sys.executable, "-c", "import xdist; print(xdist.__version__)"], text=True).strip(),
            "cpu_method": "/proc process-tree cumulative user+system ticks sampled every 10ms; child tree only",
            "percentile_method": f"nearest rank; {args.repeats} samples per arm",
        },
        "test_item_sizes": [10, 100, 1_000, 10_000],
        "repetitions": args.repeats,
        "raw_samples": raw,
        "summaries": summaries,
    }
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"environment": document["environment"], "summaries": summaries}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
