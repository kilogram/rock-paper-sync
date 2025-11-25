"""Device test harness for reMarkable device testing.

This harness provides infrastructure for interactive device tests that require
user interaction with a physical reMarkable device. Supports two modes:

**Online Mode**: Real device connected
    - User prompted for manual actions (annotating, syncing)
    - Testdata automatically captured for later replay

**Offline Mode**: No device needed
    - Pre-recorded testdata replayed via rmfakecloud
    - Enables CI testing without physical device

Usage:
    # Run all device tests in online mode (default)
    uv run pytest tests/device_bench -m device --device-mode=online

    # Run in offline mode with rmfakecloud
    uv run pytest tests/device_bench -m device --device-mode=offline

    # Replay specific test artifact
    uv run pytest tests/device_bench -m device \\
        --device-mode=offline --test-artifact=annotation_roundtrip_001

    # Run with cleanup disabled (for debugging)
    uv run pytest tests/device_bench --no-cleanup
"""

from .base import (
    DeviceTestCase,
    DeviceTestResult,
    device_test,
    requires_ocr,
    SkipTest,
)
from .bench import Bench, Colors
from .offline import OfflineEmulator
from .online import OnlineDevice
from .prompts import user_confirm, user_prompt
from .protocol import DeviceProtocol, DocumentState
from .testdata import TestArtifacts, TestdataStore, TestManifest
from .workspace import WorkspaceManager

__all__ = [
    # Base classes
    "DeviceTestCase",
    "DeviceTestResult",
    "device_test",
    "requires_ocr",
    "SkipTest",
    # Protocol and implementations
    "DeviceProtocol",
    "DocumentState",
    "OnlineDevice",
    "OfflineEmulator",
    # Testdata management
    "TestdataStore",
    "TestManifest",
    "TestArtifacts",
    # Utilities
    "Bench",
    "Colors",
    "user_prompt",
    "user_confirm",
    "WorkspaceManager",
]
