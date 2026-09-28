from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pytest_run_witness.journal import (
    MAX_LEDGER_EVENTS,
    JournalAppender,
    Verification,
    initialize_journal,
    item_digest,
    verify_journal,
)


RUN_ID = "receipt-test-run"
SALT = b"0123456789abcdef0123456789abcdef"


def _write_journal(
    path: Path,
    *,
    run_id: str = RUN_ID,
    item_count: int = 3,
    terminal_count: int = 3,
    finish: bool = True,
    collection_errors: int = 0,
) -> list[str]:
    initialize_journal(path, run_id)
    writer = JournalAppender(path)
    items = [f"a{i:031d}" for i in range(item_count)]
    ids = [item_digest(SALT, item) for item in items]
    writer.append({"type": "COLLECTION_COMPLETE", "collected_tests": len(ids), "item_ids": sorted(ids)}, durable=True)
    for item_id in ids[:terminal_count]:
        writer.append({"type": "TEST_TERMINAL", "item_id": item_id})
    if finish:
        writer.append(
            {
                "type": "SESSION_FINISHED",
                "pytest_exit_code": 0,
                "collection_errors": collection_errors,
                "incomplete_reason": None,
            },
            durable=True,
        )
    writer.close()
    return ids


def test_verifies_a_complete_journal(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    _write_journal(path)

    result = verify_journal(path, RUN_ID)

    assert result == Verification("COMPLETE", "", RUN_ID, 3, 3, 0, 0)


def test_test_failures_do_not_make_completion_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    _write_journal(path)
    # The session exit code is separate from the completeness decision.
    contents = path.read_text(encoding="utf-8").replace('"pytest_exit_code":0', '"pytest_exit_code":1')
    path.write_text(contents, encoding="utf-8")

    result = verify_journal(path, RUN_ID)

    assert result.state == "COMPLETE"
    assert result.pytest_exit_code == 1


def test_missing_terminal_event_is_a_gap(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    _write_journal(path, item_count=3, terminal_count=1)

    result = verify_journal(path, RUN_ID)

    assert result.state == "GAP"
    assert result.missing_tests == 2


def test_missing_session_finish_is_incomplete_even_if_all_items_finished(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    _write_journal(path, finish=False)

    result = verify_journal(path, RUN_ID)

    assert result.state == "INCOMPLETE"
    assert result.reason == "SESSION_NOT_FINISHED"
    assert result.missing_tests == 0


def test_empty_collection_is_a_gap(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    _write_journal(path, item_count=0, terminal_count=0)

    result = verify_journal(path, RUN_ID)

    assert result.state == "GAP"
    assert result.reason == "EMPTY_COLLECTION"


def test_collection_error_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    _write_journal(path, collection_errors=1)

    result = verify_journal(path, RUN_ID)

    assert result.state == "INCOMPLETE"
    assert result.reason == "COLLECTION_ERROR"


def test_wrong_or_stale_run_identity_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    _write_journal(path)

    result = verify_journal(path, "a-different-run")

    assert result.state == "INCOMPLETE"
    assert result.reason == "RUN_IDENTITY_MISMATCH"


def test_torn_final_line_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    _write_journal(path)
    path.write_bytes(path.read_bytes()[:-1])

    result = verify_journal(path, RUN_ID)

    assert result.state == "INCOMPLETE"
    assert result.reason == "TORN_OR_CHANGED_RECEIPT"


def test_duplicate_terminal_event_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    ids = _write_journal(path, item_count=1, terminal_count=1, finish=False)
    writer = JournalAppender(path)
    writer._next_seq = 3
    writer.append({"type": "TEST_TERMINAL", "item_id": ids[0]})
    writer.close()

    result = verify_journal(path, RUN_ID)

    assert result.state == "INCOMPLETE"
    assert result.reason == "INVALID_TERMINAL_IDENTITY"


def test_duplicate_json_keys_are_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    path.write_text(
        '{"schema_version":1,"schema_version":1,"type":"STARTED","seq":0,"run_id":"receipt-test-run"}\n',
        encoding="utf-8",
    )

    result = verify_journal(path, RUN_ID)

    assert result.state == "INCOMPLETE"
    assert result.reason == "INVALID_LEDGER_JSON"


def test_hardlinked_receipt_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    linked = tmp_path / "receipt.jsonl"
    _write_journal(source)
    try:
        os.link(source, linked)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hardlinks are unavailable: {exc}")

    result = verify_journal(linked, RUN_ID)

    assert result.state == "INCOMPLETE"
    assert result.reason == "RECEIPT_HARDLINK"


def test_excessive_event_count_is_rejected_before_json_parsing(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    path.write_bytes(b"{}\n" * (MAX_LEDGER_EVENTS + 1))

    result = verify_journal(path, RUN_ID)

    assert result.state == "INCOMPLETE"
    assert result.reason == "RECEIPT_EVENT_COUNT"


def test_bare_cr_bytes_do_not_bypass_event_count_guard(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    path.write_bytes((b"{}\r" * (MAX_LEDGER_EVENTS + 1)) + b"\n")

    result = verify_journal(path, RUN_ID)

    assert result.state == "INCOMPLETE"
    assert result.reason == "INVALID_LEDGER_JSON"


def test_node_ids_are_salted_and_not_written_to_receipt(tmp_path: Path) -> None:
    path = tmp_path / "receipt.jsonl"
    secret_nodeid = "tests/test_private.py::test_token[token=do-not-publish]"
    _write_journal(path, item_count=0, terminal_count=0, finish=False)
    assert secret_nodeid not in path.read_text(encoding="utf-8")
    assert item_digest(SALT, secret_nodeid) != item_digest(b"another salt" * 2, secret_nodeid)
