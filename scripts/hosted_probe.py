"""Produce and independently verify receipts for a disposable hosted CI run."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

try:
    from xdist import __version__ as xdist_version
except ImportError:  # pragma: no cover - producer installation includes the test extra
    xdist_version = "not-installed"


WRAPPER_INCOMPLETE = 10
VERIFIER_INVALID = 11


def _base_run_id() -> str:
    run = os.environ.get("GITHUB_RUN_ID", "local")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    return f"{run}-{attempt}"


def _scenario_run_id(base: str, scenario: str) -> str:
    return f"{base}-{scenario}"


def _write_case(root: Path, source: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "test_hosted_probe.py").write_text(source, encoding="utf-8")
    return root


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTEST_RUN_WITNESS_JOURNAL", None)
    environment.pop("PYTEST_RUN_WITNESS_RUN_ID", None)
    environment.pop("PYTEST_RUN_WITNESS_SALT", None)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _wrapper(
    case: Path,
    receipt: Path,
    run_id: str,
    *pytest_arguments: str,
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            sys.executable,
            "-m",
            "pytest_run_witness",
            "--receipt",
            str(receipt.resolve()),
            "--run-id",
            run_id,
            "--",
            "-q",
            *pytest_arguments,
        ],
        cwd=case,
    )


def _save_output(output: Path, name: str, result: subprocess.CompletedProcess[str]) -> None:
    (output / f"{name}.log").write_text(
        result.stdout + result.stderr,
        encoding="utf-8",
        errors="replace",
    )


def _read_events(receipt: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in receipt.read_text(encoding="utf-8").splitlines()]


def produce(output: Path) -> int:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    base = _base_run_id()
    results: dict[str, dict[str, object]] = {}
    started = time.monotonic()

    with tempfile.TemporaryDirectory(
        prefix="pytest-run-witness-hosted-", dir=output.parent
    ) as temporary:
        work = Path(temporary)

        normal = _write_case(
            work / "normal",
            "def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\n",
        )
        normal_receipt = output / "normal.jsonl"
        normal_result = _wrapper(
            normal, normal_receipt, _scenario_run_id(base, "normal")
        )
        _save_output(output, "normal-wrapper", normal_result)
        if normal_result.returncode != 0 or not normal_receipt.is_file():
            raise RuntimeError(
                f"normal wrapper failed ({normal_result.returncode}): "
                f"{normal_result.stdout}{normal_result.stderr}"
            )
        results["normal"] = {
            "wrapper_exit": normal_result.returncode,
            "receipt_bytes": normal_receipt.stat().st_size,
        }

        false_green_source = (
            "import os\n"
            "def test_00_exit_green(): os._exit(0)\n"
            "def test_01_never_reached(): pass\n"
            "def test_02_also_never_reached(): pass\n"
        )
        raw_child = _write_case(work / "false-green-raw", false_green_source)
        raw_result = _run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=raw_child,
        )
        _save_output(output, "false-green-raw-child", raw_result)
        false_green = _write_case(work / "false-green-wrapper", false_green_source)
        false_green_receipt = output / "false-green.jsonl"
        false_green_result = _wrapper(
            false_green,
            false_green_receipt,
            _scenario_run_id(base, "false-green"),
        )
        _save_output(output, "false-green-wrapper", false_green_result)
        events = _read_events(false_green_receipt) if false_green_receipt.is_file() else []
        collection = next(
            (event for event in events if event.get("type") == "COLLECTION_COMPLETE"),
            {},
        )
        if (
            raw_result.returncode != 0
            or false_green_result.returncode != WRAPPER_INCOMPLETE
            or collection.get("collected_tests") != 3
            or any(event.get("type") == "SESSION_FINISHED" for event in events)
        ):
            raise RuntimeError(
                "false-green producer did not observe raw child exit 0, an "
                "incomplete three-item receipt, and wrapper exit 10"
            )
        results["false_green"] = {
            "raw_child_exit": raw_result.returncode,
            "wrapper_exit": false_green_result.returncode,
            "collected": collection.get("collected_tests"),
            "session_finished": False,
            "receipt_bytes": false_green_receipt.stat().st_size,
        }

        missing_receipt = output / "missing" / "receipt.jsonl"
        missing_step = _run(
            [
                sys.executable,
                "-c",
                "print('SIMULATED_TEST_STEP=SUCCESS_WITHOUT_RECEIPT')",
            ],
            cwd=work,
        )
        _save_output(output, "missing-receipt-successful-test-step", missing_step)
        if missing_step.returncode != 0 or missing_receipt.exists():
            raise RuntimeError(
                "missing-receipt setup was not a successful step without a receipt"
            )
        results["missing_receipt_step"] = {
            "simulated_test_step_exit": missing_step.returncode,
            "receipt_created": False,
        }

        xdist_normal = _write_case(
            work / "xdist-normal",
            "".join(f"def test_{index:02}(): assert True\n" for index in range(8)),
        )
        xdist_normal_receipt = output / "xdist-normal.jsonl"
        xdist_normal_result = _wrapper(
            xdist_normal,
            xdist_normal_receipt,
            _scenario_run_id(base, "xdist-normal"),
            "-n",
            "2",
        )
        _save_output(output, "xdist-normal-wrapper", xdist_normal_result)
        if xdist_normal_result.returncode != 0 or not xdist_normal_receipt.is_file():
            raise RuntimeError(
                f"normal xdist wrapper failed ({xdist_normal_result.returncode}): "
                f"{xdist_normal_result.stdout}{xdist_normal_result.stderr}"
            )
        results["xdist_normal"] = {
            "wrapper_exit": xdist_normal_result.returncode,
            "receipt_bytes": xdist_normal_receipt.stat().st_size,
        }

        worker_loss = _write_case(
            work / "xdist-worker-loss",
            "import os\n"
            "def test_00_worker_exit(): os._exit(0)\n"
            "def test_01_other(): pass\n"
            "def test_02_other(): pass\n",
        )
        worker_loss_receipt = output / "xdist-worker-loss.jsonl"
        worker_loss_result = _wrapper(
            worker_loss,
            worker_loss_receipt,
            _scenario_run_id(base, "xdist-worker-loss"),
            "-n",
            "2",
            "--max-worker-restart=0",
        )
        _save_output(output, "xdist-worker-loss-wrapper", worker_loss_result)
        if (
            worker_loss_result.returncode != WRAPPER_INCOMPLETE
            or not worker_loss_receipt.is_file()
        ):
            raise RuntimeError(
                f"worker loss did not fail closed ({worker_loss_result.returncode}): "
                f"{worker_loss_result.stdout}{worker_loss_result.stderr}"
            )
        results["xdist_worker_loss"] = {
            "wrapper_exit": worker_loss_result.returncode,
            "receipt_bytes": worker_loss_receipt.stat().st_size,
        }

    manifest = {
        "schema_version": 1,
        "base_run_id": base,
        "python": sys.version,
        "pytest": pytest.__version__,
        "pytest_xdist": xdist_version,
        "scenarios": results,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    (output / "producer.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for name, values in results.items():
        print(f"PRODUCED_{name.upper()}={json.dumps(values, sort_keys=True)}")
    print(f"PRODUCER_ELAPSED_SECONDS={manifest['elapsed_seconds']}")
    return 0


def _is_regular_file_entry(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return (
        stat.S_ISREG(info.st_mode)
        and not bool(attributes & 0x400)
        and info.st_nlink == 1
    )


def _verify(
    receipt: Path,
    expected_run_id: str,
    *,
    working_directory: Path,
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            sys.executable,
            "-m",
            "pytest_run_witness",
            "verify",
            str(receipt.absolute()),
            "--run-id",
            expected_run_id,
        ],
        cwd=working_directory,
    )


def verify_download(input_directory: Path) -> int:
    input_directory.mkdir(parents=True, exist_ok=True)
    base = _base_run_id()
    errors: list[str] = []
    results: dict[str, dict[str, object]] = {}

    def verify_case(
        name: str,
        receipt: Path,
        run_id: str,
        expected_exit: int,
        *,
        expected_reason: str | None = None,
    ) -> None:
        result = _verify(receipt, run_id, working_directory=Path.cwd())
        output = result.stdout + result.stderr
        if expected_exit:
            passed = result.returncode == expected_exit and "INCOMPLETE" in output
        else:
            passed = result.returncode == 0 and "VERIFIED" in output
        if expected_reason is not None:
            passed = passed and expected_reason in output
        if not passed:
            errors.append(
                f"{name}: expected exit {expected_exit}, got {result.returncode}; {output.strip()}"
            )
        results[name] = {
            "expected_exit": expected_exit,
            "actual_exit": result.returncode,
            "passed": passed,
            "output": output.strip(),
        }
        label = (
            "HOSTED_XDIST_WORKER_LOSS"
            if name == "xdist_worker_loss_detected"
            else f"HOSTED_{name.upper()}"
        )
        print(f"{label}={'PASS' if passed else 'FAIL'} exit={result.returncode}")

    expected_files = {
        "normal": input_directory / "normal.jsonl",
        "false_green": input_directory / "false-green.jsonl",
        "xdist_normal": input_directory / "xdist-normal.jsonl",
        "xdist_worker_loss": input_directory / "xdist-worker-loss.jsonl",
    }
    for name, path in expected_files.items():
        if not _is_regular_file_entry(path):
            errors.append(f"expected artifact receipt is missing or unsafe: {name} ({path})")

    verify_case(
        "complete_proof",
        expected_files["normal"],
        _scenario_run_id(base, "normal"),
        0,
    )
    verify_case(
        "false_green_detected",
        expected_files["false_green"],
        _scenario_run_id(base, "false-green"),
        VERIFIER_INVALID,
        expected_reason="INCOMPLETE",
    )
    with tempfile.TemporaryDirectory(prefix="pytest-run-witness-missing-") as temporary:
        missing_receipt = Path(temporary) / "receipt.jsonl"
        verify_case(
            "missing_receipt_detected",
            missing_receipt,
            _scenario_run_id(base, "missing"),
            VERIFIER_INVALID,
            expected_reason="INCOMPLETE",
        )

    source_receipt = expected_files["normal"]
    with tempfile.TemporaryDirectory(prefix="pytest-run-witness-corrupt-") as temporary:
        corrupt_receipt = Path(temporary) / "corrupt.jsonl"
        corrupt_receipt.write_bytes(b"{not-json\n")
        verify_case(
            "corrupt_receipt_detected",
            corrupt_receipt,
            _scenario_run_id(base, "normal"),
            VERIFIER_INVALID,
            expected_reason="INCOMPLETE",
        )

    verify_case(
        "stale_receipt_detected",
        source_receipt,
        _scenario_run_id(base, "stale-other-run"),
        VERIFIER_INVALID,
        expected_reason="RUN_IDENTITY_MISMATCH",
    )
    verify_case(
        "xdist_normal",
        expected_files["xdist_normal"],
        _scenario_run_id(base, "xdist-normal"),
        0,
    )
    verify_case(
        "xdist_worker_loss_detected",
        expected_files["xdist_worker_loss"],
        _scenario_run_id(base, "xdist-worker-loss"),
        VERIFIER_INVALID,
        expected_reason="INCOMPLETE",
    )

    summary = {
        "schema_version": 1,
        "base_run_id": base,
        "results": results,
        "errors": errors,
    }
    summary_path = input_directory / "verifier.json"
    with tempfile.TemporaryDirectory(
        prefix=".pytest-run-witness-verifier-", dir=input_directory
    ) as temporary:
        temporary_summary = Path(temporary) / "verifier.json"
        temporary_summary.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_summary, summary_path)
    if errors:
        for error in errors:
            print(f"VERIFIER_ERROR={error}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    produce_parser = subparsers.add_parser("produce")
    produce_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--input", type=Path, required=True)
    options = parser.parse_args(argv)
    if options.command == "produce":
        return produce(options.output)
    return verify_download(options.input)


if __name__ == "__main__":
    raise SystemExit(main())
