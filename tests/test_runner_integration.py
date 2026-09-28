from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
RUN_ID = "pytest-run-witness-test-run"


def _environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    child_env = os.environ.copy()
    source = str(PROJECT / "src")
    child_env["PYTHONPATH"] = source + os.pathsep + child_env.get("PYTHONPATH", "")
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    project_temp = Path(
        os.environ.get("PYTEST_RUN_WITNESS_TEST_TMP", PROJECT / ".test-tmp" / "nested-temp")
    ).resolve()
    project_temp.mkdir(parents=True, exist_ok=True)
    child_env["TEMP"] = str(project_temp)
    child_env["TMP"] = str(project_temp)
    child_env["TMPDIR"] = str(project_temp)
    if extra:
        child_env.update(extra)
    return child_env


def _run(
    tmp_path: Path,
    *pytest_args: str,
    env: dict[str, str] | None = None,
    run_id: str = RUN_ID,
) -> subprocess.CompletedProcess[str]:
    receipt = tmp_path / "receipt.jsonl"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest_run_witness",
            "--receipt",
            str(receipt),
            "--run-id",
            run_id,
            "--",
            *pytest_args,
        ],
        cwd=tmp_path,
        env=_environment(env),
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
    )


def _verify(tmp_path: Path, *, run_id: str = RUN_ID, receipt: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest_run_witness", "verify", str(receipt or tmp_path / "receipt.jsonl"), "--run-id", run_id],
        cwd=tmp_path,
        env=_environment(),
        text=True,
        capture_output=True,
        check=False,
    )


def _write_suite(tmp_path: Path, source: str) -> None:
    (tmp_path / "test_sample.py").write_text(source, encoding="utf-8")


def test_normal_run_is_independently_verifiable(tmp_path: Path) -> None:
    _write_suite(tmp_path, "def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\n")

    result = _run(tmp_path, "pytest", "-q")
    verified = _verify(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERIFIED 3/3" in result.stdout
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "VERIFIED 3/3" in verified.stdout


def test_separate_verifier_rejects_missing_receipt(tmp_path: Path) -> None:
    verified = _verify(tmp_path)

    assert verified.returncode == 11
    assert "INCOMPLETE" in verified.stdout


def test_separate_verifier_requires_expected_run_identity(tmp_path: Path) -> None:
    environment = _environment()
    environment.pop("PYTEST_RUN_WITNESS_RUN_ID", None)
    verified = subprocess.run(
        [sys.executable, "-m", "pytest_run_witness", "verify", str(tmp_path / "receipt.jsonl")],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert verified.returncode == 11
    assert "expected run identity" in verified.stdout


def test_separate_verifier_rejects_stale_receipt(tmp_path: Path) -> None:
    _write_suite(tmp_path, "def test_ok(): pass\n")
    result = _run(tmp_path, "-q", run_id="previous-run-attempt")
    verified = _verify(tmp_path, run_id=RUN_ID)

    assert result.returncode == 0, result.stdout + result.stderr
    assert verified.returncode == 11
    assert "RUN_IDENTITY_MISMATCH" in verified.stdout


def test_separate_verifier_rejects_corrupted_receipt(tmp_path: Path) -> None:
    _write_suite(tmp_path, "def test_ok(): pass\n")
    result = _run(tmp_path, "-q")
    with (tmp_path / "receipt.jsonl").open("ab") as receipt_file:
        receipt_file.write(b"corruption\n")
    verified = _verify(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert verified.returncode == 11
    assert "INCOMPLETE" in verified.stdout


def test_call_and_teardown_outcomes_are_terminal(tmp_path: Path) -> None:
    _write_suite(
        tmp_path,
        "import pytest\n"
        "@pytest.mark.skip(reason='expected')\ndef test_skip(): pass\n"
        "@pytest.mark.xfail(reason='expected')\ndef test_xfail(): assert False\n"
        "@pytest.mark.xfail(reason='unexpected pass')\ndef test_xpass(): pass\n"
        "def test_assertion_failure(): assert False\n"
        "@pytest.fixture\ndef broken_setup(): raise RuntimeError('setup')\n"
        "def test_setup_failure(broken_setup): pass\n"
        "@pytest.fixture\ndef broken_teardown():\n    yield\n    raise RuntimeError('teardown')\n"
        "def test_teardown_failure(broken_teardown): pass\n"
        "def test_pass(): pass\n",
    )

    result = _run(tmp_path, "-q")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "VERIFIED 7/7" in result.stdout


def test_pytest_internal_error_exit_does_not_collide_with_incomplete_exit(
    tmp_path: Path,
) -> None:
    _write_suite(tmp_path, "def test_ok(): pass\n")
    (tmp_path / "conftest.py").write_text(
        "def pytest_sessionstart(session):\n"
        "    raise RuntimeError('controlled pytest internal error')\n",
        encoding="utf-8",
    )
    raw_pytest = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path,
        env=_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    result = _run(tmp_path, "-q")
    verified = _verify(tmp_path)

    assert raw_pytest.returncode == 3, raw_pytest.stdout + raw_pytest.stderr
    assert result.returncode == 10, result.stdout + result.stderr
    assert "SESSION_NOT_FINISHED" in result.stdout
    assert verified.returncode == 11
    assert "SESSION_NOT_FINISHED" in verified.stdout


def test_real_os_exit_zero_after_three_collected_tests_is_caught(tmp_path: Path) -> None:
    _write_suite(
        tmp_path,
        "import os\n"
        "def test_00_early_exit(): os._exit(0)\n"
        "def test_01_never_reached(): pass\n"
        "def test_02_also_never_reached(): pass\n",
    )
    raw = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path,
        env=_environment(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert raw.returncode == 0, raw.stdout + raw.stderr

    result = _run(tmp_path, "-q")
    receipt = (tmp_path / "receipt.jsonl").read_text(encoding="utf-8")
    verified = _verify(tmp_path)

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout
    assert verified.returncode == 11
    assert "SESSION_NOT_FINISHED" in verified.stdout
    assert '"collected_tests":3' in receipt
    assert "SESSION_FINISHED" not in receipt


def test_real_os_exit_zero_during_session_start_is_incomplete(tmp_path: Path) -> None:
    _write_suite(tmp_path, "def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\n")
    (tmp_path / "conftest.py").write_text(
        "import os\n"
        "def pytest_sessionstart(session): os._exit(0)\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "-q")
    verified = _verify(tmp_path)

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout
    assert "SESSION_NOT_FINISHED" in result.stdout
    assert verified.returncode == 11
    assert "SESSION_NOT_FINISHED" in verified.stdout


def test_real_os_exit_zero_during_collection_is_incomplete(tmp_path: Path) -> None:
    _write_suite(tmp_path, "def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\n")
    (tmp_path / "conftest.py").write_text(
        "import os\n"
        "def pytest_collectstart(collector):\n"
        "    if getattr(collector, 'nodeid', '').endswith('test_sample.py'): os._exit(0)\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "-q")
    verified = _verify(tmp_path)

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout
    assert "SESSION_NOT_FINISHED" in result.stdout
    assert verified.returncode == 11


def test_real_os_exit_nonzero_is_incomplete(tmp_path: Path) -> None:
    _write_suite(tmp_path, "import os\ndef test_00_exit(): os._exit(23)\ndef test_other(): pass\n")

    result = _run(tmp_path, "-q")

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout
    assert "SESSION_FINISHED" not in (tmp_path / "receipt.jsonl").read_text(encoding="utf-8")


def test_sigterm_of_pytest_child_is_incomplete(tmp_path: Path) -> None:
    _write_suite(
        tmp_path,
        "import os, signal\n"
        "def test_00_sigterm(): os.kill(os.getpid(), signal.SIGTERM)\n"
        "def test_other(): pass\n",
    )

    result = _run(tmp_path, "-q")

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout


def test_sigint_of_pytest_child_is_not_a_verified_pass(tmp_path: Path) -> None:
    _write_suite(
        tmp_path,
        "import os, signal\n"
        "def test_00_sigint(): os.kill(os.getpid(), signal.SIGINT)\n"
        "def test_other(): pass\n",
    )

    result = _run(tmp_path, "-q")

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout
    assert "VERIFIED 2/2" not in result.stdout


def test_pytest_exit_with_zero_does_not_verify_unrun_items(tmp_path: Path) -> None:
    _write_suite(
        tmp_path,
        "import pytest\n"
        "def test_00_exit(): pytest.exit('early exit', returncode=0)\n"
        "def test_01_not_run(): pass\n"
        "def test_02_not_run(): pass\n",
    )

    result = _run(tmp_path, "-q")

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout


def test_runner_success_after_collection_without_execution_is_a_gap(tmp_path: Path) -> None:
    _write_suite(tmp_path, "def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\n")
    (tmp_path / "conftest.py").write_text(
        "import os, pytest\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_runtestloop(session):\n"
        "    if os.environ.get('RETURN_SUCCESS_BEFORE_TESTS') == '1': return True\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "-q", env={"RETURN_SUCCESS_BEFORE_TESTS": "1"})

    assert result.returncode == 10
    assert "INCOMPLETE 0/3" in result.stdout


def test_lifecycle_hook_exception_before_collection_record_is_incomplete(tmp_path: Path) -> None:
    _write_suite(tmp_path, "def test_one(): pass\ndef test_two(): pass\n")
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_collection_finish(session): raise RuntimeError('hook interrupted')\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "-q")

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout


def test_all_tests_before_sessionfinish_kill_still_needs_independent_verify(tmp_path: Path) -> None:
    _write_suite(tmp_path, "def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\n")
    (tmp_path / "conftest.py").write_text(
        "import os, pytest\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    if os.environ.get('KILL_BEFORE_SESSION_FINISH') == '1': os._exit(0)\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "-q", env={"KILL_BEFORE_SESSION_FINISH": "1"})
    verified = _verify(tmp_path)

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout
    assert verified.returncode == 11
    assert "SESSION_NOT_FINISHED" in verified.stdout


@pytest.mark.parametrize("stop_option", ["-x", "--maxfail=1"])
def test_intentional_early_failure_is_a_gap_and_keeps_nonzero_gate(tmp_path: Path, stop_option: str) -> None:
    _write_suite(
        tmp_path,
        "def test_00_fail(): assert False\n"
        "def test_01_not_run(): pass\n"
        "def test_02_not_run(): pass\n",
    )

    result = _run(tmp_path, "-q", stop_option)

    assert result.returncode == 10
    assert "INCOMPLETE 1/3" in result.stdout
    assert "pytest exit code 1" not in result.stdout


def test_empty_collection_is_a_gap(tmp_path: Path) -> None:
    _write_suite(tmp_path, "value = 1\n")

    result = _run(tmp_path, "-q")

    assert result.returncode == 10
    assert "INCOMPLETE 0/0" in result.stdout


def test_collection_error_is_incomplete(tmp_path: Path) -> None:
    (tmp_path / "test_broken.py").write_text("def test_broken(:\n    pass\n", encoding="utf-8")

    result = _run(tmp_path, "-q")

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (("-k", "selected"), "VERIFIED 1/1"),
        (("-m", "fast"), "VERIFIED 1/1"),
        (("--ignore=test_ignored.py",), "VERIFIED 2/2"),
        (("test_sample.py::test_selected",), "VERIFIED 1/1"),
    ],
)
def test_intentional_selection_is_complete_within_selected_collection(
    tmp_path: Path, arguments: tuple[str, ...], expected: str
) -> None:
    _write_suite(
        tmp_path,
        "import pytest\n"
        "@pytest.mark.fast\ndef test_selected(): pass\n"
        "def test_ignored(): pass\n",
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = fast: selected tests\n", encoding="utf-8")
    (tmp_path / "test_ignored.py").write_text("def test_other_file(): pass\n", encoding="utf-8")

    result = _run(tmp_path, "-q", *arguments)

    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout


@pytest.mark.parametrize("distribution", ["load", "loadscope", "worksteal"])
def test_xdist_distribution_modes_reconcile_worker_reports(tmp_path: Path, distribution: str) -> None:
    if not importlib.util.find_spec("xdist"):
        pytest.skip("pytest-xdist is not installed")
    _write_suite(tmp_path, "".join(f"def test_{i:02}(): assert True\n" for i in range(6)))

    result = _run(tmp_path, "-q", "-n", "2", "--dist", distribution)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERIFIED 6/6" in result.stdout


@pytest.mark.parametrize("workers", ["2", "auto"])
def test_xdist_worker_count_options_reconcile(tmp_path: Path, workers: str) -> None:
    if not importlib.util.find_spec("xdist"):
        pytest.skip("pytest-xdist is not installed")
    _write_suite(tmp_path, "".join(f"def test_{i:02}(): assert True\n" for i in range(4)))
    (tmp_path / "conftest.py").write_text(
        "def pytest_xdist_auto_num_workers(config): return 2\n", encoding="utf-8"
    )

    result = _run(tmp_path, "-q", "-n", workers)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERIFIED 4/4" in result.stdout


@pytest.mark.parametrize("exit_code", [0, 41])
def test_xdist_worker_loss_is_not_a_green_complete_run(tmp_path: Path, exit_code: int) -> None:
    if not importlib.util.find_spec("xdist"):
        pytest.skip("pytest-xdist is not installed")
    _write_suite(
        tmp_path,
        f"import os\ndef test_00_worker_exit(): os._exit({exit_code})\n"
        "def test_01_other(): pass\ndef test_02_other(): pass\n",
    )

    result = _run(tmp_path, "-q", "-n", "2", "--max-worker-restart=0")

    assert result.returncode == 10
    assert "VERIFIED 3/3" not in result.stdout
    assert "INCOMPLETE" in result.stdout


def test_wrapper_death_is_detected_by_separate_verifier(tmp_path: Path) -> None:
    (tmp_path / "test_wait.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "def test_wait():\n"
        "    Path('started').write_text('yes')\n"
        "    deadline = time.monotonic() + 60\n"
        "    while not Path('stop').exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.02)\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.jsonl"
    command = [
        sys.executable,
        "-m",
        "pytest_run_witness",
        "--receipt",
        str(receipt),
        "--run-id",
        RUN_ID,
        "--",
        "-q",
    ]
    if os.name == "nt":
        process = subprocess.Popen(
            command,
            cwd=tmp_path,
            env=_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        process = subprocess.Popen(
            command,
            cwd=tmp_path,
            env=_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + 20
        marker = tmp_path / "started"
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "pytest child did not reach the hanging test"
        verified = _verify(tmp_path, receipt=receipt)
        assert verified.returncode == 11
        assert "SESSION_NOT_FINISHED" in verified.stdout
    finally:
        if process.poll() is None:
            if os.name == "nt":
                killed = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if killed.returncode != 0:
                    (tmp_path / "stop").touch()
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
                    pytest.skip(
                        "host denied Windows process-tree termination; "
                        "the independent verifier check completed before cleanup"
                    )
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    (tmp_path / "stop").touch()
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
                    pytest.skip(
                        "Windows taskkill returned success but did not stop the "
                        "wrapper process within 10 seconds"
                    )
            else:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)


def test_xdist_controller_death_after_collection_keeps_durable_denominator(tmp_path: Path) -> None:
    if not importlib.util.find_spec("xdist"):
        pytest.skip("pytest-xdist is not installed")
    _write_suite(tmp_path, "def test_one(): pass\ndef test_two(): pass\n")
    (tmp_path / "conftest.py").write_text(
        "import os, pytest\n"
        "_collections = 0\n"
        "@pytest.hookimpl(trylast=True, optionalhook=True)\n"
        "def pytest_xdist_node_collection_finished(node, ids):\n"
        "    global _collections\n"
        "    _collections += 1\n"
        "    if os.environ.get('KILL_XDIST_CONTROLLER_AFTER_COLLECTION') == '1' and _collections >= 2:\n"
        "        os._exit(0)\n",
        encoding="utf-8",
    )

    result = _run(
        tmp_path,
        "-q",
        "-n",
        "2",
        env={"KILL_XDIST_CONTROLLER_AFTER_COLLECTION": "1"},
    )
    receipt = (tmp_path / "receipt.jsonl").read_text(encoding="utf-8")
    verified = _verify(tmp_path)

    assert result.returncode == 10
    assert "INCOMPLETE" in result.stdout
    assert '"collected_tests":2' in receipt
    assert "TEST_TERMINAL" not in receipt
    assert "SESSION_FINISHED" not in receipt
    assert verified.returncode == 11
    assert "SESSION_NOT_FINISHED" in verified.stdout
    assert "0/2 terminal results recorded" in verified.stdout


def test_externally_killed_xdist_worker_is_incomplete(tmp_path: Path) -> None:
    if not importlib.util.find_spec("xdist"):
        pytest.skip("pytest-xdist is not installed")
    (tmp_path / "test_wait.py").write_text(
        "import os, time\nfrom pathlib import Path\n"
        "def test_wait():\n    Path('worker.pid').write_text(str(os.getpid()))\n    time.sleep(60)\n"
        "def test_other(): pass\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.jsonl"
    command = [
        sys.executable,
        "-m",
        "pytest_run_witness",
        "--receipt",
        str(receipt),
        "--run-id",
        RUN_ID,
        "--",
        "-q",
        "-n",
        "2",
        "--max-worker-restart=0",
    ]
    launch_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(
        command,
        cwd=tmp_path,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **launch_options,
    )
    try:
        marker = tmp_path / "worker.pid"
        deadline = time.monotonic() + 30
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "xdist worker did not reach the hanging test"
        worker_pid = int(marker.read_text(encoding="utf-8"))
        os.kill(worker_pid, signal.SIGTERM)
        output, _ = process.communicate(timeout=30)
        assert process.returncode == 10, output
        assert "VERIFIED 2/2" not in output
        assert "INCOMPLETE" in output
    finally:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
            else:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
