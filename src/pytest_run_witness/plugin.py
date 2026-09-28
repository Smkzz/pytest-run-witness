"""Incrementally record one pytest invocation's collected and terminal items."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from .journal import MAX_TESTS, JournalAppender, JournalError, item_digest

_ACTIVE: "ExecutionLedger | None" = None


def pytest_configure(config: Any) -> None:
    global _ACTIVE
    if getattr(config, "workerinput", None) is not None:
        # xdist workers send reports to the controller; only the controller
        # owns the shared invocation journal.
        _ACTIVE = None
        return

    journal_path = os.environ.get("PYTEST_RUN_WITNESS_JOURNAL")
    run_id = os.environ.get("PYTEST_RUN_WITNESS_RUN_ID")
    salt_hex = os.environ.get("PYTEST_RUN_WITNESS_SALT")
    try:
        if not journal_path or not run_id or not salt_hex:
            raise JournalError("wrapper-managed journal settings are missing")
        salt = bytes.fromhex(salt_hex)
        if len(salt) != 32:
            raise JournalError("item-identity salt is invalid")
        _ACTIVE = ExecutionLedger(config, JournalAppender(Path(journal_path)), run_id, salt)
    except (OSError, ValueError, JournalError) as exc:
        raise pytest.UsageError(f"pytest-run-witness could not open its journal: {exc}") from exc
    config.pluginmanager.register(_ACTIVE, "pytest-run-witness-ledger")


def pytest_unconfigure(config: Any) -> None:
    global _ACTIVE
    if _ACTIVE is not None and _ACTIVE.config is config:
        _ACTIVE.close()
        _ACTIVE = None


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node: Any, ids: list[str]) -> None:
    """Receive each complete worker collection on the xdist controller."""
    if _ACTIVE is not None:
        _ACTIVE.xdist_collection_finished(ids)


class ExecutionLedger:
    """In-memory identities plus durable hashed events for one pytest run."""

    def __init__(self, config: Any, writer: JournalAppender, run_id: str, salt: bytes) -> None:
        self.config = config
        self.writer = writer
        self.run_id = run_id
        self.salt = salt
        self.xdist_enabled = self._is_xdist_enabled(config)
        self.expected: dict[str, str] = {}
        self.completed: set[str] = set()
        self.item_phases: dict[str, dict[str, Any]] = {}
        self.xdist_collections: list[list[str]] = []
        self.collection_seen = False
        self.collection_errors = 0
        self.invalid_reason: str | None = None
        self.closed = False

    @staticmethod
    def _is_xdist_enabled(config: Any) -> bool:
        option = getattr(config, "option", None)
        if option is None:
            return False
        workers = getattr(option, "numprocesses", None)
        distribution = getattr(option, "dist", "no")
        return workers not in (None, 0, "0") or distribution not in (None, "no")

    def _record_collection(self, nodeids: list[str]) -> None:
        if self.collection_seen:
            self.invalid_reason = self.invalid_reason or "DUPLICATE_COLLECTION"
            return
        if len(nodeids) > MAX_TESTS:
            self.invalid_reason = self.invalid_reason or "TEST_LIMIT_EXCEEDED"
            return
        mapping: dict[str, str] = {}
        digest_owners: dict[str, str] = {}
        try:
            for nodeid in nodeids:
                if not isinstance(nodeid, str) or nodeid in mapping:
                    raise JournalError("collection contains an invalid or duplicate item identity")
                digest = item_digest(self.salt, nodeid)
                if digest in digest_owners and digest_owners[digest] != nodeid:
                    raise JournalError("item identity hash collision")
                mapping[nodeid] = digest
                digest_owners[digest] = nodeid
        except JournalError:
            self.invalid_reason = self.invalid_reason or "INVALID_COLLECTION_ITEM"
            return

        self.expected = mapping
        self.collection_seen = True
        try:
            self.writer.append(
                {
                    "type": "COLLECTION_COMPLETE",
                    "collected_tests": len(mapping),
                    "item_ids": sorted(mapping.values()),
                },
                durable=True,
            )
        except (OSError, JournalError):
            self.invalid_reason = self.invalid_reason or "JOURNAL_WRITE_FAILED"

    def pytest_collectreport(self, report: Any) -> None:
        if getattr(report, "failed", False):
            self.collection_errors += 1

    def pytest_collection_finish(self, session: Any) -> None:
        if self.xdist_enabled:
            return
        nodeids: list[str] = []
        for item in session.items:
            nodeid = getattr(item, "nodeid", None)
            if not isinstance(nodeid, str):
                self.invalid_reason = self.invalid_reason or "INVALID_COLLECTION_ITEM"
                return
            nodeids.append(nodeid)
        self._record_collection(nodeids)

    def xdist_collection_finished(self, ids: list[str]) -> None:
        if not self.xdist_enabled:
            return
        if not isinstance(ids, list) or len(ids) > MAX_TESTS or any(not isinstance(item, str) for item in ids):
            self.invalid_reason = self.invalid_reason or "INVALID_XDIST_COLLECTION"
            return
        self.xdist_collections.append(ids)

    def _finalize_xdist_collection(self) -> None:
        if self.collection_seen or not self.xdist_enabled:
            return
        if not self.xdist_collections:
            self.invalid_reason = self.invalid_reason or "NO_XDIST_COLLECTION"
            return
        first = self.xdist_collections[0]
        if any(current != first for current in self.xdist_collections[1:]):
            self.invalid_reason = self.invalid_reason or "XDIST_COLLECTION_MISMATCH"
            return
        self._record_collection(first)

    def pytest_runtest_logreport(self, report: Any) -> None:
        if not self.collection_seen and self.xdist_enabled:
            self._finalize_xdist_collection()
        nodeid = getattr(report, "nodeid", None)
        when = getattr(report, "when", None)
        outcome = getattr(report, "outcome", None)
        if not self.collection_seen:
            self.invalid_reason = self.invalid_reason or "NO_COLLECTION"
            return
        if not isinstance(nodeid, str) or nodeid not in self.expected:
            self.invalid_reason = self.invalid_reason or "REPORT_FOR_UNCOLLECTED_TEST"
            return
        if when not in {"setup", "call", "teardown"} or outcome not in {"passed", "failed", "skipped"}:
            self.invalid_reason = self.invalid_reason or "INVALID_TEST_REPORT"
            return
        if nodeid in self.completed:
            # Rerun plugins may execute a completed item again. One fully
            # finished pytest item protocol is sufficient for this ledger.
            return

        if when == "setup":
            if nodeid in self.item_phases:
                self.invalid_reason = self.invalid_reason or "INVALID_TEST_PHASE_ORDER"
                return
            self.item_phases[nodeid] = {"setup_outcome": outcome, "call_seen": False}
            return

        phase = self.item_phases.get(nodeid)
        if phase is None:
            self.invalid_reason = self.invalid_reason or "INVALID_TEST_PHASE_ORDER"
            return
        if when == "call":
            if phase["setup_outcome"] != "passed" or phase["call_seen"]:
                self.invalid_reason = self.invalid_reason or "INVALID_TEST_PHASE_ORDER"
                return
            phase["call_seen"] = True
            return

        # A teardown report ends the item protocol even when setup/call or
        # teardown failed or was skipped. It also covers xfail/xpass outcomes.
        if phase["setup_outcome"] == "passed" and not phase["call_seen"]:
            self.invalid_reason = self.invalid_reason or "INVALID_TEST_PHASE_ORDER"
            return
        digest = self.expected[nodeid]
        try:
            self.writer.append({"type": "TEST_TERMINAL", "item_id": digest})
        except (OSError, JournalError):
            self.invalid_reason = self.invalid_reason or "JOURNAL_WRITE_FAILED"
            return
        self.completed.add(nodeid)
        self.item_phases.pop(nodeid, None)

    def _finalize_collection(self) -> None:
        if self.collection_seen:
            return
        if self.xdist_enabled:
            self._finalize_xdist_collection()
        else:
            self.invalid_reason = self.invalid_reason or "NO_COLLECTION"

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        self._finalize_collection()
        try:
            self.writer.append(
                {
                    "type": "SESSION_FINISHED",
                    "pytest_exit_code": int(exitstatus),
                    "collection_errors": self.collection_errors,
                    "incomplete_reason": self.invalid_reason,
                },
                durable=True,
            )
        except (OSError, JournalError):
            self.invalid_reason = self.invalid_reason or "JOURNAL_WRITE_FAILED"

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
            except OSError:
                pass
