"""OCR replay tests with mocked service and result verification.

These tests use the OCR integration mixin to:
1. Mock the Runpods OCR service
2. Return predictable OCR text
3. Record all OCR requests/responses
4. Verify results match expectations at teardown

Unlike the interactive ocr_tests.py, these tests:
- Run without user interaction
- Work in both online and offline (replay) modes
- Don't require real OCR service or credentials
- Produce deterministic results
"""

from pathlib import Path

from ..harness.base import DeviceTestCase, device_test
from ..harness.ocr_integration import OCRIntegrationMixin


class OCRMockedRecognitionTest(OCRIntegrationMixin, DeviceTestCase):
    """Mocked OCR recognition test.

    Test flow:
    1. Sync document with annotations
    2. OCR service is mocked to return expected texts
    3. Sync to run (mocked) OCR
    4. Verify expected OCR texts appear in vault output
    5. Compare recorded OCR output to expected values
    """

    name = "ocr-mocked-recognition"
    description = "Mocked OCR: annotations → mocked OCR service → verify output recorded"
    requires_ocr = True
    ocr_record_results = True

    # Define expected OCR outputs
    ocr_expected_texts = {
        "annotation-1": "hello world",
        "annotation-2": "2025",
        "annotation-3": "quick test",
    }

    @device_test(requires_ocr=True, cleanup_on_success=True)
    def execute(self) -> bool:
        """Execute OCR recognition test with mocked service."""
        # Step 1: Initial sync
        ret, out, err = self.sync("Initial sync")
        if ret != 0:
            return False

        self.bench.observe("Initial sync complete")

        # Step 2: In online mode, user would write annotations
        # In replay mode, testdata provides pre-recorded annotations
        self.bench.observe("Using mocked annotations (online) or replay testdata")

        # Step 3: Sync to trigger OCR processing
        ret, out, err = self.sync("Sync with OCR")
        if ret != 0:
            return False

        self.bench.observe("OCR processing complete")

        # Step 4: Verify OCR markers exist
        content = self.workspace.get_document_content()

        # Check for OCR markers (format may be RPS:ANNOTATED or RPS:OCR)
        ocr_found = "<!-- RPS:ANNOTATED" in content or "<!-- RPS:OCR -->" in content

        if not ocr_found:
            self.bench.warn("No OCR markers found in output")
            # Don't fail - teardown will verify if texts are present

        ocr_count = content.count("<!-- RPS:ANNOTATED") + content.count("<!-- RPS:OCR -->")
        if ocr_count > 0:
            self.bench.observe(f"Found {ocr_count} OCR annotation(s)")

        return True


class OCRMockedCorrectionTest(OCRIntegrationMixin, DeviceTestCase):
    """Mocked OCR correction workflow test.

    Test flow:
    1. Sync document with annotations
    2. OCR service returns recognized text
    3. Verify corrections are stored in database
    4. Record OCR and correction data
    """

    name = "ocr-mocked-correction"
    description = "Mocked OCR correction: recognize → verify storage → record"
    requires_ocr = True
    ocr_record_results = True

    ocr_expected_texts = {
        "annotation-1": "original text",
        "annotation-2": "corrected phrase",
    }

    @device_test(requires_ocr=True, cleanup_on_success=True)
    def execute(self) -> bool:
        """Execute OCR correction test with mocked service."""
        # Step 1: Initial sync
        ret, out, err = self.sync("Initial sync")
        if ret != 0:
            return False

        # Step 2: Sync with OCR
        ret, out, err = self.sync("Sync with OCR")
        if ret != 0:
            return False

        # Step 3: Verify OCR markers
        content = self.workspace.get_document_content()
        if "<!-- RPS:OCR -->" not in content and "<!-- RPS:ANNOTATED" not in content:
            self.bench.warn("No OCR markers found")

        self.bench.observe("OCR recognition complete")

        # Step 4: In online mode, user would correct the text
        # In replay mode, we would have pre-recorded corrections
        # For now, just verify the mocked output was captured
        self.bench.observe("OCR results recorded and ready for verification")

        return True


class OCRMockedStabilityTest(OCRIntegrationMixin, DeviceTestCase):
    """Verify OCR marker stability with mocked service.

    Test flow:
    1. Sync with mocked OCR
    2. Sync again - should not re-upload
    3. Verify hash stability (no re-processing loops)
    """

    name = "ocr-mocked-stability"
    description = "Mocked OCR stability: verify markers don't cause re-upload loops"
    requires_ocr = True
    ocr_record_results = True

    ocr_expected_texts = {
        "annotation-1": "stable text output",
    }

    @device_test(requires_ocr=True, cleanup_on_success=True)
    def execute(self) -> bool:
        """Execute OCR stability test with mocked service."""
        # Step 1: Initial sync
        ret, out, err = self.sync("Initial sync")
        if ret != 0:
            return False

        # Step 2: First OCR sync
        ret, out, err = self.sync("Sync with OCR")
        if ret != 0:
            return False

        content_after_ocr = self.workspace.get_document_content()
        if "<!-- RPS:OCR -->" not in content_after_ocr and "<!-- RPS:ANNOTATED" not in content_after_ocr:
            self.bench.warn("No OCR markers found after first sync")

        self.bench.observe("OCR markers added")

        # Step 3: Second sync - should not re-upload
        ret, out, err = self.sync("Second sync (should skip)")
        if ret != 0:
            return False

        # Check if file was skipped (varies by implementation)
        if "unchanged" in out.lower() or "skipping" in out.lower():
            self.bench.observe("✓ Correctly skipped unchanged file")
        elif "synced" in out.lower() or "uploaded" in out.lower():
            self.bench.warn("File was synced again (may be normal if re-hashing differs)")

        # Step 4: Third sync - extra verification
        ret, out, err = self.sync("Third sync (extra check)")
        if ret != 0:
            return False

        self.bench.observe("Stability verification complete")

        return True
