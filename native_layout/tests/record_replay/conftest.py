"""Conftest for native_layout record/replay tests.

Provides minimal fixture stubs and collection hooks so that offline tests
pass when run via ``uv run pytest native_layout/``.  The full device
harness lives in ``tests/record_replay/conftest.py``; online tests should
be run from there.
"""

import pytest


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Skip online-only and device tests when running outside the harness."""
    for item in items:
        if "online_only" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="online tests require the device harness"))
        if "device" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="device tests require the device harness"))
