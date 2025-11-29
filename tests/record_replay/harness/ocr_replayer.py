"""OCR response replayer for offline device tests.

Replays previously recorded OCR responses during offline test execution.
Uses fuzzy image matching (pHash) to handle rendering variations.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import imagehash
from PIL import Image

if TYPE_CHECKING:
    from rock_paper_sync.ocr.protocol import OCRRequest, OCRResult

logger = logging.getLogger(__name__)


class OCRReplayError(Exception):
    """Raised when OCR replay fails."""

    pass


@dataclass
class OCRExpectation:
    """Expected OCR result for validation."""

    text: str
    confidence_min: float = 0.0
    paragraph_index: int | None = None


class OCRReplayer:
    """Replays recorded OCR responses during offline test execution.

    Loads recordings from testdata and matches incoming requests using
    image similarity (pHash). Validates results against inline expectations.

    Usage:
        # Initialize with expectations from fixture
        expectations = parse_ocr_expectations(fixture_markdown)
        replayer = OCRReplayer(testdata_dir, expectations)

        # Load all phase recordings
        replayer.load_recordings(test_id)

        # Start replaying for a phase
        replayer.start_phase(phase_num=1, phase_name="post_sync")

        # Return recorded results for matching requests
        result = replayer.recognize(request)
    """

    def __init__(
        self,
        testdata_dir: Path,
        expectations: list[OCRExpectation] | None = None,
    ) -> None:
        """Initialize OCR replayer.

        Args:
            testdata_dir: Base testdata directory
            expectations: List of expected OCR results for validation
        """
        self.testdata_dir = testdata_dir
        self.expectations = expectations or []
        self.recordings_by_phase: dict[int, dict] = {}
        self.current_phase_num: int | None = None
        self.phash_threshold = 10  # Hamming distance threshold

    def load_recordings(self, test_id: str) -> None:
        """Load all phase recordings for a test.

        Args:
            test_id: Test identifier

        Raises:
            FileNotFoundError: If test not found
        """
        test_dir = self.testdata_dir / test_id
        if not test_dir.exists():
            raise FileNotFoundError(f"Test not found: {test_id}")

        phases_dir = test_dir / "phases"
        if not phases_dir.exists():
            logger.warning(f"No phases directory for test {test_id}")
            return

        # Load recordings from each phase
        for phase_dir in sorted(phases_dir.iterdir()):
            if not phase_dir.is_dir():
                continue

            ocr_file = phase_dir / "ocr_recordings.json"
            if not ocr_file.exists():
                continue

            # Parse phase number from directory name (phase_N_name)
            try:
                phase_num = int(phase_dir.name.split("_")[1])
            except (IndexError, ValueError):
                logger.warning(f"Invalid phase directory name: {phase_dir.name}")
                continue

            # Load recordings
            with open(ocr_file) as f:
                data = json.load(f)
                self.recordings_by_phase[phase_num] = data
                logger.debug(
                    f"Loaded {len(data.get('recordings', []))} OCR recordings for phase {phase_num}"
                )

    def start_phase(self, phase_num: int, phase_name: str) -> None:
        """Start replay for a specific phase.

        Args:
            phase_num: Phase number
            phase_name: Phase name (for logging)
        """
        self.current_phase_num = phase_num
        logger.debug(f"Started OCR replay for phase {phase_num} ({phase_name})")

    def recognize(self, request: "OCRRequest") -> "OCRResult":
        """Replay OCR result for a request.

        Matches request image against recordings using fuzzy matching.

        Args:
            request: OCR request

        Returns:
            OCR result from recordings

        Raises:
            OCRReplayError: If no matching recording found
        """
        if self.current_phase_num is None:
            raise OCRReplayError("No active phase - call start_phase() first")

        recordings_data = self.recordings_by_phase.get(self.current_phase_num)
        if not recordings_data:
            raise OCRReplayError(f"No recordings found for phase {self.current_phase_num}")

        # Find matching recording
        recording = self._find_matching_recording(request.image, recordings_data)

        # Convert recording to OCRResult
        from rock_paper_sync.ocr.protocol import BoundingBox, OCRResult

        result = OCRResult(
            annotation_uuid=recording["annotation_uuid"],
            text=recording["recognized_text"],
            confidence=recording["confidence"],
            model_version=recording["model_version"],
            bounding_box=BoundingBox(**recording["bounding_box"])
            if recording.get("bounding_box")
            else request.bounding_box,
            context=request.context,
            processing_time_ms=0,  # Replay is instant
        )

        logger.debug(f"Replayed OCR: {request.annotation_uuid[:8]}... -> '{result.text[:30]}...'")

        return result

    def recognize_batch(self, requests: list["OCRRequest"]) -> list["OCRResult"]:
        """Replay OCR results for a batch of requests.

        Args:
            requests: List of OCR requests

        Returns:
            List of OCR results
        """
        return [self.recognize(req) for req in requests]

    def _find_matching_recording(self, image_data: bytes, recordings_data: dict) -> dict:
        """Find recording that matches the image.

        Uses exact SHA256 match first (fast path), then fuzzy pHash matching.

        Args:
            image_data: PNG image bytes
            recordings_data: Recordings data for current phase

        Returns:
            Matching recording dictionary

        Raises:
            OCRReplayError: If no match found within threshold
        """
        recordings = recordings_data.get("recordings", [])
        if not recordings:
            raise OCRReplayError("No recordings available for matching")

        # Compute hashes
        sha256 = hashlib.sha256(image_data).hexdigest()

        # Try exact match first (fast path)
        for rec in recordings:
            if rec["image_sha256"] == sha256:
                logger.debug(f"Exact SHA256 match for image {sha256[:8]}")
                return rec

        # Fuzzy match with pHash
        phash = imagehash.phash(Image.open(BytesIO(image_data)))
        best_match = None
        best_distance = float("inf")

        for rec in recordings:
            rec_phash = imagehash.hex_to_hash(rec["image_phash"])
            distance = phash - rec_phash  # Hamming distance

            if distance <= self.phash_threshold and distance < best_distance:
                best_match = rec
                best_distance = distance

        if best_match:
            logger.debug(
                f"Fuzzy pHash match: distance={best_distance} "
                f"for image {sha256[:8]} -> {best_match['image_sha256'][:8]}"
            )
            return best_match

        # No match found
        raise OCRReplayError(
            f"No matching recording for image {sha256[:8]}... "
            f"(tried {len(recordings)} recordings, pHash threshold={self.phash_threshold})"
        )

    def validate_results(self, markdown_content: str) -> dict[str, bool]:
        """Validate OCR results against expectations.

        Checks if expected texts appear in the markdown content.

        Args:
            markdown_content: Final markdown content after OCR processing

        Returns:
            Dictionary mapping expectation text to validation result
        """
        results = {}

        for expectation in self.expectations:
            # Check if expected text appears in content (case-insensitive)
            found = expectation.text.lower() in markdown_content.lower()
            results[expectation.text] = found

            if found:
                logger.debug(f"✓ Found expected OCR: '{expectation.text}'")
            else:
                logger.warning(f"✗ Missing expected OCR: '{expectation.text}'")

        return results

    def get_model_info(self):
        """Return mock model info for replay mode."""
        from rock_paper_sync.ocr.protocol import ModelInfo

        return ModelInfo(
            version="replay-v1",
            base_model="recorded",
            is_fine_tuned=False,
            dataset_version=None,
            created_at=None,
            metrics={},
        )

    def health_check(self) -> bool:
        """Always healthy in replay mode."""
        return True


def parse_ocr_expectations(markdown_text: str) -> list[OCRExpectation]:
    """Parse inline OCR expectations from fixture markdown.

    Looks for HTML comment markers:
        <!-- OCR_EXPECT: text="hello world" confidence_min=0.7 -->

    Args:
        markdown_text: Fixture markdown content

    Returns:
        List of OCR expectations
    """
    pattern = r"<!-- OCR_EXPECT:\s*(.*?)\s*-->"
    expectations = []

    for match in re.finditer(pattern, markdown_text):
        attrs_str = match.group(1)

        # Parse attributes
        attrs = {}
        for attr_match in re.finditer(r'(\w+)="([^"]*)"', attrs_str):
            attrs[attr_match.group(1)] = attr_match.group(2)

        # Also parse numeric attributes without quotes
        for attr_match in re.finditer(r"(\w+)=(\d+\.?\d*)", attrs_str):
            if attr_match.group(1) not in attrs:  # Don't override quoted values
                attrs[attr_match.group(1)] = attr_match.group(2)

        # Create expectation
        expectation = OCRExpectation(
            text=attrs.get("text", ""),
            confidence_min=float(attrs.get("confidence_min", 0.0)),
            paragraph_index=int(attrs["paragraph_index"]) if "paragraph_index" in attrs else None,
        )

        expectations.append(expectation)
        logger.debug(f"Parsed OCR expectation: text='{expectation.text}'")

    return expectations
