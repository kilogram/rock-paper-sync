"""OCR integration for record/replay device tests.

Provides OCR service recording (online) and replay (offline) for device tests.
Allows tests to:
1. Record real OCR service calls during online test execution
2. Replay recorded responses during offline emulation
3. Validate OCR results against inline expectations in fixtures
4. Use fuzzy image matching (pHash) for robust replay

Mode Detection:
- Online mode (OnlineDevice): Wraps real OCR service to record calls
- Offline mode (OfflineEmulator): Replays from recorded testdata
- Mock mode (no device): Falls back to simple mocking for unit tests
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from .ocr_recorder import OCRRecorder
from .ocr_replayer import OCRReplayer, parse_ocr_expectations

if TYPE_CHECKING:
    from .protocol import DeviceInteractionProtocol

logger = logging.getLogger(__name__)


@dataclass
class OCRRequest:
    """Records an OCR request during test execution."""

    annotation_uuid: str
    image_hash: str  # Hash of image data for deduplication
    paragraph_index: int
    timestamp: str


@dataclass
class OCRResult:
    """Records an OCR result during test execution."""

    annotation_uuid: str
    text: str
    confidence: float
    model_version: str
    timestamp: str


@dataclass
class OCRTestRecording:
    """Complete OCR recording for a test."""

    test_id: str
    test_name: str
    requests: list[OCRRequest]
    results: list[OCRResult]
    created_at: str

    def to_dict(self):
        """Convert to JSON-serializable dict."""
        return {
            "test_id": self.test_id,
            "test_name": self.test_name,
            "requests": [asdict(r) for r in self.requests],
            "results": [asdict(r) for r in self.results],
            "created_at": self.created_at,
        }


class OCRIntegrationMixin:
    """Mixin for DeviceTestCase to add OCR service mocking and recording.

    When mixed into a DeviceTestCase subclass, automatically:
    1. Mocks the OCRService in setup()
    2. Records all OCR requests/responses during test
    3. Saves recordings to testdata directory
    4. Compares recorded output to expected values in teardown()

    Usage:
        class MyOCRTest(OCRIntegrationMixin, DeviceTestCase):
            name = "my-ocr-test"
            ocr_expected_texts = {
                "annotation-1": "expected text here",
                "annotation-2": "another phrase",
            }

            def execute(self) -> bool:
                # Test code - OCR service is already mocked
                self.sync("Sync with OCR")
                return True
    """

    # Override these in subclass
    ocr_expected_texts: dict[str, str] = {}  # annotation_uuid -> expected OCR text
    ocr_record_results: bool = True  # Whether to record OCR results to testdata

    def __init__(self, *args, **kwargs):
        """Initialize OCR integration."""
        super().__init__(*args, **kwargs)
        self._ocr_recording = OCRTestRecording(
            test_id=self.name,
            test_name=self.name,
            requests=[],
            results=[],
            created_at=datetime.now().isoformat(),
        )
        self._ocr_service_mock: MagicMock | None = None

    def setup(self) -> None:
        """Setup OCR mocking before test execution."""
        # Call parent setup if it exists
        if hasattr(super(), "setup"):
            super().setup()

        # Skip if OCR not required
        if not getattr(self, "requires_ocr", False):
            logger.debug("OCR not required for this test - skipping OCR setup")
            return

        # Create and inject mocked OCR service
        self._setup_ocr_service()

    def _setup_ocr_service(self) -> None:
        """Create and configure OCR service based on test mode.

        Mode detection:
        - Online mode: Wrap real OCR service with OCRRecorder
        - Offline mode: Use OCRReplayer with recorded testdata
        - Mock mode: Fall back to simple mocking for unit tests
        """
        # Detect mode from device type
        device = getattr(self, "device", None)
        mode = self._detect_mode(device)

        logger.info(f"Setting up OCR in {mode} mode")

        if mode == "online":
            self._setup_online_recording()
        elif mode == "offline":
            self._setup_offline_replay()
        else:
            self._setup_mock_service()

    def _detect_mode(self, device: "DeviceInteractionProtocol | None") -> str:
        """Detect test mode from device type.

        Args:
            device: Device interaction object

        Returns:
            "online", "offline", or "mock"
        """
        if device is None:
            return "mock"

        # Import here to avoid circular dependencies
        from .offline import OfflineEmulator
        from .online import OnlineDevice

        if isinstance(device, OnlineDevice):
            return "online"
        elif isinstance(device, OfflineEmulator):
            return "offline"
        else:
            return "mock"

    def _setup_online_recording(self) -> None:
        """Setup OCR recording for online mode with real service."""
        from unittest.mock import patch

        from rock_paper_sync.ocr.factory import create_ocr_service

        # Create real OCR service
        real_service = create_ocr_service(self.config)

        # Wrap with recorder
        testdata_dir = getattr(self, "testdata_dir", Path("testdata"))
        self.ocr_recorder = OCRRecorder(real_service, testdata_dir)

        # Patch factory to return recorder
        self._ocr_patch = patch(
            "rock_paper_sync.ocr.factory.create_ocr_service",
            return_value=self.ocr_recorder,
        )
        self._ocr_patch.start()

        logger.info("OCR service wrapped with recorder for online mode")
        self.bench.observe("OCR recording enabled (online mode)")

    def _setup_offline_replay(self) -> None:
        """Setup OCR replay for offline mode from recordings."""
        from unittest.mock import patch

        # Parse expectations from fixture if available
        expectations = []
        if hasattr(self, "fixture_path") and self.fixture_path.exists():
            fixture_content = self.fixture_path.read_text()
            expectations = parse_ocr_expectations(fixture_content)
            logger.debug(f"Parsed {len(expectations)} OCR expectations from fixture")

        # Create replayer
        testdata_dir = getattr(self, "testdata_dir", Path("testdata"))
        self.ocr_replayer = OCRReplayer(testdata_dir, expectations)

        # Load recordings for this test
        if hasattr(self, "name"):
            try:
                self.ocr_replayer.load_recordings(self.name)
                logger.info(f"Loaded OCR recordings for test '{self.name}'")
            except FileNotFoundError:
                logger.warning(f"No OCR recordings found for test '{self.name}'")

        # Patch factory to return replayer
        self._ocr_patch = patch(
            "rock_paper_sync.ocr.factory.create_ocr_service",
            return_value=self.ocr_replayer,
        )
        self._ocr_patch.start()

        logger.info("OCR service using replayer for offline mode")
        self.bench.observe("OCR replay enabled (offline mode)")

    def _setup_mock_service(self) -> None:
        """Create and patch the mocked OCR service for unit tests."""
        from unittest.mock import patch

        from rock_paper_sync.ocr.protocol import OCRResult as OCRResultProto

        # Create mock service
        self._ocr_service_mock = MagicMock()

        # Define mock recognition function
        def mock_recognize(request):
            """Mock recognize - record request and return result."""
            import hashlib

            # Record request
            image_hash = hashlib.sha256(
                request.image if isinstance(request.image, bytes) else b""
            ).hexdigest()[:8]
            self._ocr_recording.requests.append(
                OCRRequest(
                    annotation_uuid=request.annotation_uuid,
                    image_hash=image_hash,
                    paragraph_index=request.context.paragraph_index if request.context else -1,
                    timestamp=datetime.now().isoformat(),
                )
            )

            # Return result from expected texts or default
            expected_text = self.ocr_expected_texts.get(request.annotation_uuid, "recognized text")

            result = OCRResultProto(
                annotation_uuid=request.annotation_uuid,
                text=expected_text,
                confidence=0.95,
                model_version="mocked-test-v1",
                bounding_box=request.bounding_box,
                context=request.context,
                processing_time_ms=50,
            )

            # Record result
            self._ocr_recording.results.append(
                OCRResult(
                    annotation_uuid=request.annotation_uuid,
                    text=expected_text,
                    confidence=0.95,
                    model_version="mocked-test-v1",
                    timestamp=datetime.now().isoformat(),
                )
            )

            return result

        def mock_recognize_batch(requests):
            """Mock batch recognition."""
            return [mock_recognize(req) for req in requests]

        # Configure mock
        self._ocr_service_mock.recognize = MagicMock(side_effect=mock_recognize)
        self._ocr_service_mock.recognize_batch = MagicMock(side_effect=mock_recognize_batch)
        self._ocr_service_mock.health_check = MagicMock(return_value=True)

        # Patch the factory to return our mock
        self._ocr_patch = patch(
            "rock_paper_sync.ocr.factory.create_ocr_service",
            return_value=self._ocr_service_mock,
        )
        self._ocr_patch.start()

        logger.info("OCR service mocked for unit test mode")
        self.bench.observe("OCR service mocked and configured")

    def teardown(self) -> None:
        """Verify OCR results and save recordings after test execution."""
        # Stop OCR patching first (if it was started)
        if hasattr(self, "_ocr_patch") and self._ocr_patch is not None:
            self._ocr_patch.stop()
            self._ocr_patch = None

        # Call parent teardown if it exists
        if hasattr(super(), "teardown"):
            super().teardown()

        # Skip verification if OCR not required
        if not getattr(self, "requires_ocr", False):
            return

        # Verify OCR results
        self._verify_ocr_results()

        # Save recordings if enabled (only in mock mode)
        if self.ocr_record_results and hasattr(self, "_ocr_recording"):
            self._save_ocr_recording()

    def _verify_ocr_results(self) -> None:
        """Verify OCR results match expectations."""
        if not self.ocr_expected_texts:
            self.bench.observe("No OCR expectations set - skipping verification")
            return

        self.bench.subheader("OCR Result Verification")

        # Get document content
        content = self.workspace.get_document_content()

        # Verify expected texts appear in output
        verified_count = 0
        for annotation_uuid, expected_text in self.ocr_expected_texts.items():
            if expected_text.lower() in content.lower():
                self.bench.observe(f"✓ Found expected OCR: '{expected_text}'")
                verified_count += 1
            else:
                self.bench.error(f"✗ Missing expected OCR: '{expected_text}'")

        success_rate = (
            verified_count / len(self.ocr_expected_texts) if self.ocr_expected_texts else 0
        )
        self.bench.observe(
            f"OCR verification: {verified_count}/{len(self.ocr_expected_texts)} ({success_rate*100:.0f}%)"
        )

        if success_rate < 1.0:
            self.bench.warn(
                f"Only {verified_count}/{len(self.ocr_expected_texts)} expected OCR texts found"
            )

    def _save_ocr_recording(self) -> None:
        """Save OCR recording to testdata directory."""
        # Get testdata directory from workspace
        testdata_dir = (
            Path(self.workspace.workspace_dir).parent / "testdata" / "record_replay" / "ocr_results"
        )
        testdata_dir.mkdir(parents=True, exist_ok=True)

        # Save recording
        recording_file = testdata_dir / f"{self.name}.json"
        with open(recording_file, "w") as f:
            json.dump(self._ocr_recording.to_dict(), f, indent=2)

        self.bench.observe(f"OCR recording saved: {recording_file}")

    def get_ocr_recording(self) -> OCRTestRecording:
        """Get the OCR recording for this test.

        Useful for custom verification in subclasses.
        """
        return self._ocr_recording
