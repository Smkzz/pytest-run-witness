from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
PROBE_SPEC = importlib.util.spec_from_file_location(
    "pytest_run_witness_hosted_probe", PROJECT / "scripts" / "hosted_probe.py"
)
assert PROBE_SPEC is not None and PROBE_SPEC.loader is not None
hosted_probe = importlib.util.module_from_spec(PROBE_SPEC)
PROBE_SPEC.loader.exec_module(hosted_probe)


def test_corrupt_receipt_scenario_does_not_follow_input_symlink(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_directory = tmp_path / "artifacts"
    assert hosted_probe.produce(input_directory) == 0

    sentinel = tmp_path / "outside-sentinel.jsonl"
    original = b"keep this file unchanged\n"
    sentinel.write_bytes(original)
    corrupt_receipt = input_directory / "corrupt.jsonl"
    summary_sentinel = tmp_path / "outside-verifier-sentinel.json"
    summary_original = b"keep the verifier target unchanged\n"
    summary_sentinel.write_bytes(summary_original)
    verifier_summary = input_directory / "verifier.json"
    try:
        corrupt_receipt.symlink_to(sentinel)
        verifier_summary.symlink_to(summary_sentinel)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")

    assert hosted_probe.verify_download(input_directory) == 0
    capsys.readouterr()

    assert corrupt_receipt.is_symlink()
    assert sentinel.read_bytes() == original
    assert verifier_summary.is_file()
    assert not verifier_summary.is_symlink()
    assert json.loads(verifier_summary.read_text(encoding="utf-8"))["errors"] == []
    assert summary_sentinel.read_bytes() == summary_original


def test_verify_preserves_receipt_symlink_for_journal_guard(tmp_path: Path) -> None:
    input_directory = tmp_path / "artifacts"
    assert hosted_probe.produce(input_directory) == 0
    base_run_id = json.loads(
        (input_directory / "producer.json").read_text(encoding="utf-8")
    )["base_run_id"]

    receipt = input_directory / "normal.jsonl"
    outside_receipt = tmp_path / "outside-normal.jsonl"
    original = receipt.read_bytes()
    outside_receipt.write_bytes(original)
    receipt.unlink()
    try:
        receipt.symlink_to(outside_receipt)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")

    result = hosted_probe._verify(
        receipt,
        hosted_probe._scenario_run_id(base_run_id, "normal"),
        working_directory=PROJECT,
    )
    output = result.stdout + result.stderr

    assert result.returncode == hosted_probe.VERIFIER_INVALID
    assert "RECEIPT_LINK" in output
    assert receipt.is_symlink()
    assert not hosted_probe._is_regular_file_entry(receipt)
    assert outside_receipt.read_bytes() == original


def test_verify_download_does_not_traverse_artifact_missing_directory_link(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "artifacts"
    assert hosted_probe.produce(input_directory) == 0

    outside = tmp_path / "outside-missing"
    outside.mkdir()
    outside_receipt = outside / "receipt.jsonl"
    outside_receipt.write_bytes(b"outside sentinel\n")
    missing_directory = input_directory / "missing"
    try:
        missing_directory.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    assert hosted_probe.verify_download(input_directory) == 0

    summary = json.loads((input_directory / "verifier.json").read_text(encoding="utf-8"))
    assert summary["errors"] == []
    assert outside_receipt.read_bytes() == b"outside sentinel\n"


def test_verify_download_rejects_hardlinked_expected_receipt(tmp_path: Path) -> None:
    input_directory = tmp_path / "artifacts"
    assert hosted_probe.produce(input_directory) == 0

    receipt = input_directory / "normal.jsonl"
    outside_receipt = tmp_path / "outside-normal.jsonl"
    receipt.replace(outside_receipt)
    original = outside_receipt.read_bytes()
    try:
        os.link(outside_receipt, receipt)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hardlinks are unavailable: {exc}")

    assert not hosted_probe._is_regular_file_entry(receipt)
    assert hosted_probe.verify_download(input_directory) == 1

    summary = json.loads((input_directory / "verifier.json").read_text(encoding="utf-8"))
    assert any("normal" in error and "unsafe" in error for error in summary["errors"])
    assert summary["results"]["complete_proof"]["actual_exit"] == hosted_probe.VERIFIER_INVALID
    assert "RECEIPT_HARDLINK" in summary["results"]["complete_proof"]["output"]
    assert outside_receipt.read_bytes() == original
