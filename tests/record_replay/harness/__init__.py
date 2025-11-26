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
    uv run pytest tests/record_replay -m device --device-mode=online

    # Run in offline mode with rmfakecloud
    uv run pytest tests/record_replay -m device --device-mode=offline

    # Replay specific test artifact
    uv run pytest tests/record_replay -m device \\
        --device-mode=offline --test-artifact=annotation_roundtrip_001

    # Run with cleanup disabled (for debugging)
    uv run pytest tests/record_replay --no-cleanup
"""

from .base import (
    DeviceTestCase,
    DeviceTestResult,
    device_test,
    requires_ocr,
    SkipTest,
)
from .logging import Bench
from .output import Colors
from .offline import OfflineEmulator
from .ocr_integration import OCRIntegrationMixin, OCRTestRecording
from .online import OnlineDevice
from .prompts import user_confirm, user_prompt
from .protocol import DeviceProtocol, DeviceInteractionManager, DocumentState
from .testdata import TestArtifacts, TestdataStore, TestManifest
from .vault_manager import VaultInteractionManager, VaultOperation
from .vault_online import OnlineVault
from .vault_offline import OfflineVault
from .workspace import WorkspaceManager

__all__ = [
    # Base classes
    "DeviceTestCase",
    "DeviceTestResult",
    "device_test",
    "requires_ocr",
    "SkipTest",
    # Device protocol and implementations
    "DeviceProtocol",  # Backward compatibility alias
    "DeviceInteractionManager",  # New name
    "DocumentState",
    "OnlineDevice",
    "OfflineEmulator",
    # Vault protocol and implementations
    "VaultInteractionManager",
    "VaultOperation",
    "OnlineVault",
    "OfflineVault",
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
    # OCR integration
    "OCRIntegrationMixin",
    "OCRTestRecording",
]
