"""OCR integration for record/replay device tests.

Provides OCR service mocking and result recording for device tests.
Allows tests to:
1. Mock OCRService to return predictable results
2. Record all OCR requests and responses during test
3. Compare recorded OCR output to expected values at teardown
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    pass


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
            return

        # Create and inject mocked OCR service
        self._setup_ocr_service()

    def _setup_ocr_service(self) -> None:
        """Create and patch the mocked OCR service."""
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

        self.bench.observe("OCR service mocked and configured")

    def teardown(self) -> None:
        """Verify OCR results and save recordings after test execution."""
        # Call parent teardown if it exists
        if hasattr(super(), "teardown"):
            super().teardown()

        # Stop OCR patching
        if hasattr(self, "_ocr_patch"):
            self._ocr_patch.stop()

        # Skip verification if OCR not required
        if not getattr(self, "requires_ocr", False):
            return

        # Verify OCR results
        self._verify_ocr_results()

        # Save recordings if enabled
        if self.ocr_record_results:
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
