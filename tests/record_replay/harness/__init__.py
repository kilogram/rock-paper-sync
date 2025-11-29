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
    SkipTestError,
    device_test,
    requires_ocr,
)
from .logging import Bench
from .ocr_integration import OCRIntegrationMixin, OCRTestRecording
from .offline import OfflineEmulator
from .online import OnlineDevice
from .output import Colors
from .prompts import user_confirm, user_prompt
from .protocol import (
    DeviceInteractionManager,  # Backward compatibility
    DeviceInteractionProtocol,
    DeviceProtocol,  # Backward compatibility
    DocumentState,
    derive_test_id,
)
from .testdata import TestArtifacts, TestdataStore, TestManifest
from .vault_manager import VaultInteractionManager, VaultOperation
from .vault_offline import OfflineVault
from .vault_online import OnlineVault
from .workspace import WorkspaceManager

__all__ = [
    # Base classes
    "DeviceTestCase",
    "DeviceTestResult",
    "device_test",
    "requires_ocr",
    "SkipTestError",
    # Device protocol and implementations
    "DeviceInteractionProtocol",  # Primary protocol
    "DeviceProtocol",  # Backward compatibility alias
    "DeviceInteractionManager",  # Backward compatibility alias
    "DocumentState",
    "derive_test_id",
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
