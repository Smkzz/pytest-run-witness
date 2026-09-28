"""Parent wrapper and independent verifier for one pytest invocation."""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

from .journal import JournalError, Verification, initialize_journal, validate_run_id, verify_journal

INCOMPLETE_EXIT = 10
VERIFY_ERROR_EXIT = 11


def _display(result: Verification) -> None:
    collected = result.collected_tests
    terminal = result.terminal_tests
    if result.state == "COMPLETE":
        assert collected is not None and terminal is not None
        print(f"VERIFIED {terminal}/{collected}")
    elif result.state == "GAP":
        assert collected is not None and terminal is not None
        missing = result.missing_tests or 0
        print(f"INCOMPLETE {terminal}/{collected}; {missing} collected tests have no terminal result ({result.reason})")
    else:
        if collected is not None and terminal is not None:
            print(f"INCOMPLETE {result.reason}; {terminal}/{collected} terminal results recorded")
        else:
            print(f"INCOMPLETE {result.reason}")


def _verify_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pytest-run-witness verify")
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--run-id", default=os.environ.get("PYTEST_RUN_WITNESS_RUN_ID"))
    try:
        options = parser.parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code)
    if not options.run_id:
        print("INCOMPLETE expected run identity; pass --run-id or set PYTEST_RUN_WITNESS_RUN_ID")
        return VERIFY_ERROR_EXIT
    try:
        expected = validate_run_id(options.run_id)
    except JournalError:
        print("INCOMPLETE invalid expected run identity")
        return VERIFY_ERROR_EXIT
    result = verify_journal(options.receipt, expected)
    _display(result)
    return 0 if result.state == "COMPLETE" else VERIFY_ERROR_EXIT


def _run_main(argv: list[str]) -> int:
    has_separator = "--" in argv
    if has_separator:
        separator = argv.index("--")
        wrapper_args, pytest_args = argv[:separator], argv[separator + 1 :]
    else:
        # Preserve the compact legacy form: without a separator every token
        # is forwarded to pytest.
        wrapper_args, pytest_args = [], argv
    if has_separator and pytest_args and Path(pytest_args[0]).name.lower() in {"pytest", "pytest.exe"}:
        pytest_args = pytest_args[1:]

    parser = argparse.ArgumentParser(prog="pytest-run-witness")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--run-id")
    try:
        options = parser.parse_args(wrapper_args)
    except SystemExit as exc:
        return int(exc.code)
    if not pytest_args:
        print("Usage: pytest-run-witness [--receipt PATH] [--run-id ID] -- [pytest arguments ...]")
        return 2

    run_id = options.run_id or uuid.uuid4().hex
    try:
        validate_run_id(run_id)
    except JournalError as exc:
        print(f"INCOMPLETE invalid run identity: {exc}")
        return INCOMPLETE_EXIT
    receipt = options.receipt or Path.cwd() / ".pytest-run-witness" / f"receipt-{uuid.uuid4().hex}.jsonl"
    receipt = receipt.absolute()
    try:
        initialize_journal(receipt, run_id)
    except (OSError, JournalError) as exc:
        print(f"INCOMPLETE could not initialize receipt: {exc}")
        return INCOMPLETE_EXIT

    salt = secrets.token_bytes(32)
    environment = os.environ.copy()
    environment["PYTEST_RUN_WITNESS_JOURNAL"] = str(receipt)
    environment["PYTEST_RUN_WITNESS_RUN_ID"] = run_id
    environment["PYTEST_RUN_WITNESS_SALT"] = salt.hex()
    command = [
        sys.executable,
        "-m",
        "pytest",
        *pytest_args,
        "-p",
        "pytest_run_witness.plugin",
    ]
    print(f"RUN_ID={run_id}")
    print(f"RECEIPT={receipt}")
    try:
        child = subprocess.run(command, cwd=Path.cwd(), env=environment, check=False)
    except KeyboardInterrupt:
        print("INCOMPLETE wrapper interrupted before it could verify pytest")
        return INCOMPLETE_EXIT
    except OSError as exc:
        print(f"INCOMPLETE could not start pytest: {exc}")
        return INCOMPLETE_EXIT

    result = verify_journal(receipt, run_id)
    if result.state == "COMPLETE" and result.pytest_exit_code != child.returncode:
        result = Verification(
            state="INCOMPLETE",
            reason="PROCESS_EXIT_MISMATCH",
            run_id=run_id,
            collected_tests=result.collected_tests,
            terminal_tests=result.terminal_tests,
            pytest_exit_code=result.pytest_exit_code,
        )
    _display(result)
    if result.state != "COMPLETE":
        return INCOMPLETE_EXIT
    return child.returncode


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "verify":
        return _verify_main(arguments)
    if arguments in ([], ["-h"], ["--help"]):
        print("Usage: pytest-run-witness [--receipt PATH] [--run-id ID] -- [pytest arguments ...]")
        print("       pytest-run-witness verify RECEIPT --run-id ID")
        return 0 if arguments else 2
    return _run_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
