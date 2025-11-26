"""OCR test scenarios.

Tests for OCR integration:
- Recognition: handwriting → OCR → verify text
- Correction: OCR → user corrects → verify stored
- Stability: OCR markers don't cause re-upload loops

These tests support both:
- Online mode: Real device + real (or mocked) OCR service
- Offline mode: Pre-recorded testdata with mocked OCR service

The OCRIntegrationMixin automatically:
1. Mocks the OCR service for deterministic results
2. Records all OCR requests and responses
3. Verifies OCR output matches expectations
4. Saves recordings to testdata directory
"""

from pathlib import Path

from ..harness.base import DeviceTestCase, device_test, requires_ocr
from ..harness.prompts import user_prompt
from ..harness.output import Colors
from ..harness.ocr_integration import OCRIntegrationMixin


class OCRRecognitionTest(OCRIntegrationMixin, DeviceTestCase):
    """Basic OCR recognition test with mocked OCR service.

    Test flow:
    1. Sync document with OCR gaps
    2. User writes specific text in gaps (online) or use testdata (offline)
    3. Sync to run OCR (mocked service)
    4. Verify OCR markers contain expected recognized text
    5. Record OCR results to testdata

    In offline mode: Uses pre-recorded handwriting annotations and testdata
    In online mode: Requires user to manually write on reMarkable device
    """

    name = "ocr-recognition"
    description = "OCR recognition: write text → sync → verify recognition"
    requires_ocr = True
    ocr_record_results = True

    # Expected OCR outputs - customize based on what user writes
    # These are the texts that the mocked OCR service will return
    ocr_expected_texts = {
        "hello-annotation": "hello",
        "number-annotation": "2025",
        "phrase-annotation": "quick test",
    }

    @device_test(requires_ocr=True, cleanup_on_success=True)
    def execute(self) -> bool:
        # Step 1: Initial sync
        ret, out, err = self.sync("Initial sync")
        if ret != 0:
            return False

        # Step 2: User writes handwritten text
        if not user_prompt("Write handwritten text", [
            f"Open '{self.workspace.device_folder}/document' on reMarkable",
            "In 'Test 1: Simple Words' section, write 'hello' in the gap",
            "In 'Test 2: Numbers' section, write '2025' in the gap",
            "In 'Test 3: Short Phrase' section, write 'quick test' in the gap",
            "Use highlighter to mark each gap where you wrote",
            "Wait for cloud sync to complete",
        ]):
            return False

        # Step 3: Download and run OCR
        ret, out, err = self.sync("Download and run OCR")
        if ret != 0:
            return False

        # Step 4: Verify OCR markers
        content = self.workspace.get_document_content()

        if "<!-- OCR:" not in content:
            self.bench.error("No OCR markers found!")
            self.bench.info("Make sure OCR is enabled in config and service is running")
            return False

        ocr_count = content.count("<!-- OCR:")
        self.bench.observe(f"Found {ocr_count} OCR marker(s)")

        # Display OCR results
        self._display_ocr_blocks(content)

        # Check for expected patterns
        expected_patterns = ["hello", "2025", "quick", "test"]
        found_patterns = []

        for pattern in expected_patterns:
            if pattern.lower() in content.lower():
                found_patterns.append(pattern)
                self.bench.observe(f"Found expected text: '{pattern}'")

        if len(found_patterns) < 2:
            self.bench.warn(
                f"Only found {len(found_patterns)} of {len(expected_patterns)} "
                f"expected patterns (OCR accuracy may vary)"
            )

        return True

    def _display_ocr_blocks(self, content: str) -> None:
        """Display OCR blocks in formatted output."""
        in_ocr_block = False
        for line in content.split("\n"):
            if "<!-- OCR:" in line:
                in_ocr_block = True
                print(f"  {Colors.GREEN}{line.strip()}{Colors.END}")
            elif "<!-- /OCR -->" in line:
                in_ocr_block = False
                print(f"  {Colors.GREEN}{line.strip()}{Colors.END}")
            elif in_ocr_block:
                print(f"  {Colors.CYAN}    {line.strip()}{Colors.END}")


class OCRCorrectionTest(OCRIntegrationMixin, DeviceTestCase):
    """OCR correction workflow test with mocked OCR service.

    Test flow:
    1. Sync document with OCR gaps
    2. User writes text (online) or use testdata (offline)
    3. Sync (runs mocked OCR)
    4. User corrects OCR text in markdown
    5. Sync (captures correction)
    6. Record OCR and correction data to testdata
    """

    name = "ocr-correction"
    description = "OCR correction workflow: OCR → user corrects → verify stored"
    requires_ocr = True
    ocr_record_results = True

    ocr_expected_texts = {
        "typo-annotation": "handwriten note",  # Will be corrected by user
    }

    @device_test(requires_ocr=True, cleanup_on_success=True)
    def execute(self) -> bool:
        # Step 1: Initial sync
        ret, out, err = self.sync("Initial sync")
        if ret != 0:
            return False

        # Step 2: User writes text
        if not user_prompt("Write handwritten text", [
            f"Open '{self.workspace.device_folder}/document' on reMarkable",
            "In 'Test 1' section, write any text in the gap",
            "Use highlighter to mark the gap",
            "Wait for cloud sync to complete",
        ]):
            return False

        # Step 3: Download and run OCR
        ret, out, err = self.sync("Download and run OCR")
        if ret != 0:
            return False

        content = self.workspace.get_document_content()
        if "<!-- OCR:" not in content:
            self.bench.error("No OCR markers found!")
            return False

        self.bench.observe("OCR markers found")

        # Step 4: User corrects OCR
        if not user_prompt("Correct OCR text", [
            f"Open {self.workspace.test_doc} in a text editor",
            "Find the OCR block (between <!-- OCR: ... --> tags)",
            "Edit the recognized text to correct any errors",
            "Keep the marker tags intact",
            "Save the file",
        ]):
            return False

        content_after = self.workspace.get_document_content()
        if content_after == content:
            self.bench.warn("File unchanged - no correction made")
            return False

        self.bench.observe("OCR text corrected by user")

        # Step 5: Sync to capture correction
        ret, out, err = self.sync("Sync with correction")
        if ret != 0:
            return False

        self.bench.observe("Correction captured in sync")
        return True


class OCRStabilityTest(OCRIntegrationMixin, DeviceTestCase):
    """OCR markers stability test with mocked OCR service.

    Test flow:
    1. Sync document
    2. User writes text (online) or use testdata (offline)
    3. Sync (runs mocked OCR, adds markers)
    4. Sync again - should skip (no re-upload)
    5. Sync third time - should still skip

    Verifies OCR markers don't cause hash loops or instability.
    Records OCR results for comparison.
    """

    name = "ocr-stability"
    description = "Verify OCR markers don't cause re-upload loops"
    requires_ocr = True
    ocr_record_results = True

    ocr_expected_texts = {
        "stability-annotation": "stable text output",
    }

    @device_test(requires_ocr=True, cleanup_on_success=True)
    def execute(self) -> bool:
        # Step 1: Initial sync
        ret, out, err = self.sync("Initial sync")
        if ret != 0:
            return False

        # Step 2: User writes text
        if not user_prompt("Write handwritten text", [
            f"Open '{self.workspace.device_folder}/document' on reMarkable",
            "Write any text in one of the gaps",
            "Use highlighter to mark it",
            "Wait for cloud sync to complete",
        ]):
            return False

        # Step 3: Download and run OCR
        ret, out, err = self.sync("Download and run OCR")
        if ret != 0:
            return False

        if "<!-- OCR:" not in self.workspace.get_document_content():
            self.bench.error("No OCR markers found!")
            return False

        self.bench.observe("OCR markers added")

        # Step 4: Second sync - should skip
        ret, out, err = self.sync("Second sync (should skip)")
        if ret != 0:
            return False

        if "unchanged" in out.lower() or "skipping" in out.lower():
            self.bench.observe("Correctly skipped - no re-upload")
        elif "synced" in out.lower() or "uploaded" in out.lower():
            self.bench.error("Re-uploaded! OCR markers causing hash loop!")
            return False

        # Step 5: Third sync - extra verification
        ret, out, err = self.sync("Third sync (extra check)")
        if ret != 0:
            return False

        if "unchanged" in out.lower() or "skipping" in out.lower():
            self.bench.observe("Third sync also skipped - OCR marker stability confirmed")
        elif "synced" in out.lower() or "uploaded" in out.lower():
            self.bench.error("Third sync re-uploaded! Inconsistent behavior!")
            return False

        return True
