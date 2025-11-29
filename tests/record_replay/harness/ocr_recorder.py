"""OCR request/response recorder for online device tests.

Records OCR service calls during online test execution for later replay.
Captures:
- OCR requests with annotation images
- OCR responses with recognized text
- Image hashes (SHA256 + pHash) for fuzzy matching during replay
"""

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import imagehash
from PIL import Image

if TYPE_CHECKING:
    from rock_paper_sync.ocr.protocol import OCRRequest, OCRResult, OCRServiceProtocol

logger = logging.getLogger(__name__)


@dataclass
class OCRRecording:
    """A single OCR request/response recording."""

    annotation_uuid: str
    image_sha256: str  # Exact hash for fast matching
    image_phash: str  # Perceptual hash for fuzzy matching (hex string)
    recognized_text: str
    confidence: float
    model_version: str
    paragraph_index: int
    timestamp: str
    bounding_box: dict | None = None
    context: dict | None = None


@dataclass
class PhaseOCRRecordings:
    """Complete OCR recordings for a single test phase."""

    phase_number: int
    phase_name: str
    recordings: list[OCRRecording] = field(default_factory=list)
    images: dict[str, str] = field(default_factory=dict)  # sha256 -> base64 encoded PNG
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dictionary."""
        return {
            "phase_number": self.phase_number,
            "phase_name": self.phase_name,
            "recordings": [asdict(r) for r in self.recordings],
            "images": self.images,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PhaseOCRRecordings":
        """Load from dictionary."""
        recordings = [OCRRecording(**r) for r in data.get("recordings", [])]
        return cls(
            phase_number=data["phase_number"],
            phase_name=data["phase_name"],
            recordings=recordings,
            images=data.get("images", {}),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )


class OCRRecorder:
    """Records OCR service calls during online test execution.

    Wraps a real OCR service to capture all requests and responses.
    Stores recordings in testdata phase structure for later replay.

    Usage:
        recorder = OCRRecorder(real_ocr_service, testdata_dir)

        # Start recording for a phase
        recorder.start_phase(phase_num=1, phase_name="post_sync")

        # OCR service calls are recorded automatically
        result = recorder.recognize(request)

        # Save recordings
        recorder.save_phase(test_id)
    """

    def __init__(self, ocr_service: "OCRServiceProtocol", testdata_dir: Path) -> None:
        """Initialize OCR recorder.

        Args:
            ocr_service: Real OCR service to wrap
            testdata_dir: Base testdata directory for saving recordings
        """
        self.ocr_service = ocr_service
        self.testdata_dir = testdata_dir
        self.current_phase: PhaseOCRRecordings | None = None

    def start_phase(self, phase_num: int, phase_name: str) -> None:
        """Start recording for a new phase.

        Args:
            phase_num: Phase number
            phase_name: Phase name (e.g., "post_sync", "final")
        """
        self.current_phase = PhaseOCRRecordings(
            phase_number=phase_num,
            phase_name=phase_name,
        )
        logger.debug(f"Started OCR recording for phase {phase_num} ({phase_name})")

    def recognize(self, request: "OCRRequest") -> "OCRResult":
        """Recognize text and record the request/response.

        Wraps the real OCR service recognize() method.

        Args:
            request: OCR request

        Returns:
            OCR result from real service
        """
        # Call real service
        result = self.ocr_service.recognize(request)

        # Record if phase is active
        if self.current_phase is not None:
            self._record_call(request, result)

        return result

    def recognize_batch(self, requests: list["OCRRequest"]) -> list["OCRResult"]:
        """Recognize batch and record all request/response pairs.

        Wraps the real OCR service recognize_batch() method.

        Args:
            requests: List of OCR requests

        Returns:
            List of OCR results from real service
        """
        # Call real service
        results = self.ocr_service.recognize_batch(requests)

        # Record all pairs if phase is active
        if self.current_phase is not None:
            for request, result in zip(requests, results):
                self._record_call(request, result)

        return results

    def _record_call(self, request: "OCRRequest", result: "OCRResult") -> None:
        """Record a single OCR request/response pair.

        Args:
            request: OCR request
            result: OCR result
        """
        if self.current_phase is None:
            logger.warning("OCR call made outside of phase recording - ignoring")
            return

        # Compute image hashes
        sha256 = hashlib.sha256(request.image).hexdigest()
        phash = imagehash.phash(Image.open(BytesIO(request.image)))

        # Create recording
        recording = OCRRecording(
            annotation_uuid=request.annotation_uuid,
            image_sha256=sha256,
            image_phash=str(phash),
            recognized_text=result.text,
            confidence=result.confidence,
            model_version=result.model_version,
            paragraph_index=(request.context.paragraph_index if request.context else -1),
            timestamp=datetime.now().isoformat(),
            bounding_box={
                "x": request.bounding_box.x,
                "y": request.bounding_box.y,
                "width": request.bounding_box.width,
                "height": request.bounding_box.height,
            }
            if request.bounding_box
            else None,
            context={
                "document_id": request.context.document_id,
                "page_number": request.context.page_number,
                "paragraph_index": request.context.paragraph_index,
                "paragraph_text": request.context.paragraph_text[:100],  # Truncate
            }
            if request.context
            else None,
        )

        self.current_phase.recordings.append(recording)

        # Store image (deduplicated by SHA256)
        if sha256 not in self.current_phase.images:
            import base64

            self.current_phase.images[sha256] = base64.b64encode(request.image).decode("utf-8")

        logger.debug(
            f"Recorded OCR call: {request.annotation_uuid[:8]}... -> '{result.text[:30]}...'"
        )

    def save_phase(self, test_id: str) -> Path | None:
        """Save recordings for current phase to testdata.

        Args:
            test_id: Test identifier

        Returns:
            Path to saved recordings file, or None if no phase active
        """
        if self.current_phase is None:
            logger.warning("No active phase to save")
            return None

        # Determine phase directory
        phase_dir = (
            self.testdata_dir
            / test_id
            / "phases"
            / f"phase_{self.current_phase.phase_number}_{self.current_phase.phase_name}"
        )
        phase_dir.mkdir(parents=True, exist_ok=True)

        # Save recordings
        recordings_file = phase_dir / "ocr_recordings.json"
        with open(recordings_file, "w") as f:
            json.dump(self.current_phase.to_dict(), f, indent=2)

        logger.info(
            f"Saved {len(self.current_phase.recordings)} OCR recordings to {recordings_file}"
        )

        return recordings_file

    def get_model_info(self):
        """Delegate to wrapped service."""
        return self.ocr_service.get_model_info()

    def health_check(self) -> bool:
        """Delegate to wrapped service."""
        return self.ocr_service.health_check()
