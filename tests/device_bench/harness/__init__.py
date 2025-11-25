"""Device test harness for reMarkable device testing.

This harness provides infrastructure for interactive device tests that require
user interaction with a physical reMarkable device.

Usage:
    # Run all device tests
    uv run python -m pytest tests/device_bench -m device

    # Run specific scenario
    uv run python -m pytest tests/device_bench -k annotation_roundtrip

    # Run with cleanup disabled (for debugging)
    uv run python -m pytest tests/device_bench --no-cleanup
"""

from .base import (
    DeviceTestCase,
    DeviceTestResult,
    device_test,
    requires_ocr,
)
from .bench import Bench, Colors
from .prompts import user_prompt, user_confirm
from .workspace import WorkspaceManager

__all__ = [
    "DeviceTestCase",
    "DeviceTestResult",
    "device_test",
    "requires_ocr",
    "Bench",
    "Colors",
    "user_prompt",
    "user_confirm",
    "WorkspaceManager",
]
