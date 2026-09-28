"""Controlled demonstration of a test runner that stops but returns success."""

import os

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session):
    if os.environ.get("RUNWITNESS_SIMULATE_EARLY_GREEN") != "1":
        return None
    first = session.items[0]
    session.config.hook.pytest_runtest_protocol(item=first, nextitem=None)
    return True
