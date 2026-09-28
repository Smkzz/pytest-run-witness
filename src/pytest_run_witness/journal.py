"""Bounded, append-only completion journal shared by the wrapper and plugin."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_TESTS = 100_000
MAX_NODEID_CHARS = 16_384
MAX_RUN_ID_CHARS = 128
MAX_LEDGER_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_LEDGER_EVENTS = MAX_TESTS + 3

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ITEM_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class JournalError(ValueError):
    """Raised when a journal cannot safely be created or appended."""


@dataclass(frozen=True)
class Verification:
    state: str
    reason: str
    run_id: str | None = None
    collected_tests: int | None = None
    terminal_tests: int | None = None
    missing_tests: int | None = None
    pytest_exit_code: int | None = None


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or not _RUN_ID_RE.fullmatch(value):
        raise JournalError("run identity must be 1-128 simple ASCII letters, digits, '.', '_', ':', or '-'")
    return value


def item_digest(salt: bytes, nodeid: str) -> str:
    if not isinstance(nodeid, str) or not nodeid or len(nodeid) > MAX_NODEID_CHARS:
        raise JournalError("pytest item identity is invalid or too long")
    try:
        encoded = nodeid.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise JournalError("pytest item identity is not valid UTF-8") from exc
    return hmac.new(salt, encoded, hashlib.sha256).hexdigest()[:32]


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_point = bool(attributes & 0x400)
    return stat.S_ISLNK(info.st_mode) or reparse_point


def _encoded_line(value: dict[str, Any]) -> bytes:
    try:
        encoded = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise JournalError("journal event cannot be encoded") from exc
    if len(encoded) > MAX_LINE_BYTES:
        raise JournalError("journal event exceeds the line-size limit")
    return encoded


def initialize_journal(path: Path, run_id: str) -> None:
    """Atomically replace the selected output with a durable STARTED record."""
    validate_run_id(run_id)
    destination = Path(path).absolute()
    if _is_link_or_reparse(destination):
        raise JournalError("receipt path is a link or reparse point")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.is_dir():
        raise JournalError("receipt path is a directory")

    started = _encoded_line(
        {"schema_version": SCHEMA_VERSION, "type": "STARTED", "seq": 0, "run_id": run_id}
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".pytest-run-witness-", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(started)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


class JournalAppender:
    """Write compact records immediately; sync collection and final records."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if _is_link_or_reparse(self.path):
            raise JournalError("receipt path is a link or reparse point")
        flags = os.O_WRONLY | os.O_APPEND
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        self._fd = os.open(self.path, flags)
        self._size = os.fstat(self._fd).st_size
        self._next_seq = 1
        self._closed = False
        if self._size > MAX_LEDGER_BYTES:
            self.close()
            raise JournalError("receipt exceeds the size limit")

    def append(self, event: dict[str, Any], *, durable: bool = False) -> None:
        if self._closed:
            raise JournalError("journal is closed")
        record = {"seq": self._next_seq, **event}
        encoded = _encoded_line(record)
        if self._size + len(encoded) > MAX_LEDGER_BYTES:
            raise JournalError("journal exceeds the size limit")
        view = memoryview(encoded)
        while view:
            written = os.write(self._fd, view)
            if written <= 0:
                raise OSError("journal write made no progress")
            view = view[written:]
        self._size += len(encoded)
        self._next_seq += 1
        if durable:
            os.fsync(self._fd)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self._fd)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JournalError("duplicate JSON key")
        result[key] = value
    return result


def _integer(value: Any, *, minimum: int = 0, maximum: int = 2**31 - 1) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _incomplete(reason: str, **details: Any) -> Verification:
    return Verification(state="INCOMPLETE", reason=reason, **details)


def verify_journal(path: Path, expected_run_id: str) -> Verification:
    """Validate the entire journal and reconcile terminal item identities."""
    try:
        validate_run_id(expected_run_id)
        source = Path(path)
        info = source.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400):
            return _incomplete("RECEIPT_LINK")
        if not stat.S_ISREG(info.st_mode):
            return _incomplete("RECEIPT_MISSING_OR_UNREADABLE")
        if info.st_nlink != 1:
            return _incomplete("RECEIPT_HARDLINK")
        size = info.st_size
        if size <= 0 or size > MAX_LEDGER_BYTES:
            return _incomplete("RECEIPT_SIZE")
        raw = source.read_bytes()
    except (OSError, JournalError, ValueError):
        return _incomplete("RECEIPT_MISSING_OR_UNREADABLE")

    if len(raw) != size or not raw.endswith(b"\n"):
        return _incomplete("TORN_OR_CHANGED_RECEIPT")
    if raw.count(b"\n") > MAX_LEDGER_EVENTS:
        return _incomplete("RECEIPT_EVENT_COUNT")

    lines = raw[:-1].split(b"\n")
    if not lines or any(not line or len(line) + 1 > MAX_LINE_BYTES for line in lines):
        return _incomplete("INVALID_LEDGER_LINE")
    try:
        events = [json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError, JournalError):
        return _incomplete("INVALID_LEDGER_JSON")
    if any(not isinstance(event, dict) for event in events):
        return _incomplete("INVALID_LEDGER_EVENT")

    start = events[0]
    if (
        set(start) != {"schema_version", "type", "seq", "run_id"}
        or type(start.get("schema_version")) is not int
        or start.get("schema_version") != SCHEMA_VERSION
        or start.get("type") != "STARTED"
        or type(start.get("seq")) is not int
        or start.get("seq") != 0
        or not isinstance(start.get("run_id"), str)
    ):
        return _incomplete("INVALID_START_EVENT")
    try:
        run_id = validate_run_id(start["run_id"])
    except JournalError:
        return _incomplete("INVALID_START_IDENTITY")
    if run_id != expected_run_id:
        return _incomplete("RUN_IDENTITY_MISMATCH", run_id=run_id)

    expected_ids: set[str] | None = None
    terminal_ids: set[str] = set()
    session: dict[str, Any] | None = None
    for sequence, event in enumerate(events[1:], start=1):
        if type(event.get("seq")) is not int or event.get("seq") != sequence:
            return _incomplete("INVALID_EVENT_SEQUENCE", run_id=run_id)
        event_type = event.get("type")
        if event_type == "COLLECTION_COMPLETE":
            if expected_ids is not None or terminal_ids or session is not None:
                return _incomplete("INVALID_COLLECTION_ORDER", run_id=run_id)
            if set(event) != {"seq", "type", "collected_tests", "item_ids"}:
                return _incomplete("INVALID_COLLECTION_EVENT", run_id=run_id)
            ids = event.get("item_ids")
            if (
                not _integer(event.get("collected_tests"), maximum=MAX_TESTS)
                or not isinstance(ids, list)
                or len(ids) > MAX_TESTS
                or event["collected_tests"] != len(ids)
                or any(not isinstance(item, str) or not _ITEM_ID_RE.fullmatch(item) for item in ids)
                or len(set(ids)) != len(ids)
            ):
                return _incomplete("INVALID_COLLECTION_ITEMS", run_id=run_id)
            expected_ids = set(ids)
        elif event_type == "TEST_TERMINAL":
            if expected_ids is None or session is not None or set(event) != {"seq", "type", "item_id"}:
                return _incomplete("INVALID_TERMINAL_EVENT", run_id=run_id)
            item_id = event.get("item_id")
            if (
                not isinstance(item_id, str)
                or not _ITEM_ID_RE.fullmatch(item_id)
                or item_id not in expected_ids
                or item_id in terminal_ids
            ):
                return _incomplete("INVALID_TERMINAL_IDENTITY", run_id=run_id)
            terminal_ids.add(item_id)
        elif event_type == "SESSION_FINISHED":
            if session is not None or set(event) != {
                "seq", "type", "pytest_exit_code", "collection_errors", "incomplete_reason"
            }:
                return _incomplete("INVALID_SESSION_EVENT", run_id=run_id)
            code = event.get("pytest_exit_code")
            errors = event.get("collection_errors")
            reason = event.get("incomplete_reason")
            if (
                not _integer(code, minimum=-255, maximum=255)
                or not _integer(errors, maximum=MAX_TESTS)
                or reason is not None
                and (not isinstance(reason, str) or not re.fullmatch(r"[A-Z0-9_]{1,64}", reason))
            ):
                return _incomplete("INVALID_SESSION_FIELDS", run_id=run_id)
            session = event
        else:
            return _incomplete("UNKNOWN_LEDGER_EVENT", run_id=run_id)

    if session is None:
        collected = len(expected_ids) if expected_ids is not None else None
        terminal = len(terminal_ids) if expected_ids is not None else None
        missing = collected - terminal if collected is not None and terminal is not None else None
        return _incomplete(
            "SESSION_NOT_FINISHED",
            run_id=run_id,
            collected_tests=collected,
            terminal_tests=terminal,
            missing_tests=missing,
        )

    pytest_exit_code = session["pytest_exit_code"]
    if expected_ids is None:
        return _incomplete("COLLECTION_NOT_FINISHED", run_id=run_id, pytest_exit_code=pytest_exit_code)
    collected_count = len(expected_ids)
    terminal_count = len(terminal_ids)
    missing_count = collected_count - terminal_count
    details = {
        "run_id": run_id,
        "collected_tests": collected_count,
        "terminal_tests": terminal_count,
        "missing_tests": missing_count,
        "pytest_exit_code": pytest_exit_code,
    }
    if session["incomplete_reason"] is not None:
        return _incomplete(session["incomplete_reason"], **details)
    if session["collection_errors"]:
        return _incomplete("COLLECTION_ERROR", **details)
    if collected_count == 0:
        return Verification(state="GAP", reason="EMPTY_COLLECTION", **details)
    if missing_count:
        return Verification(state="GAP", reason="MISSING_TERMINAL_RESULTS", **details)
    return Verification(state="COMPLETE", reason="", **details)
