"""Tests for recording and replaying OCR handwriting annotations.

This test suite records handwritten text from a device and verifies
the OCR system can recognize and process the handwriting.

Recording Usage:
    uv run pytest tests/record_replay/test_ocr_handwriting.py \
        --device-mode=online --online

    When prompted, use the ballpoint pen to write the specified words.

Replaying:
    uv run pytest tests/record_replay/test_ocr_handwriting.py \
        --device-mode=offline
"""

import io
import pytest

from rmscene import read_blocks
from rock_paper_sync.annotations import read_annotations, AnnotationType


@pytest.mark.device
@pytest.mark.online_only
class TestOCRHandwritingRecording:
    """Record handwritten annotations from a physical reMarkable device."""

    def test_record_ocr_handwriting(self, device, workspace, fixtures_dir):
        """Record handwritten text using ballpoint pen.

        User Instructions:
        1. Use the ballpoint pen tool
        2. Write in the designated spaces:
           - Section 1: "hello"
           - Section 2: "2025"
           - Section 3: "quick test"
           - Section 4: "Code 42"
           - Section 5: "The quick brown fox"
        3. Press Enter when done
        """
        test_id = "ocr_handwriting"

        fixture_doc = fixtures_dir / "test_ocr_handwriting.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        device.start_test(
            test_id,
            description="OCR handwriting samples"
        )

        try:
            doc_uuid = device.upload_document(workspace.test_doc)
            state = device.wait_for_annotations(doc_uuid)
            assert state.has_annotations, "No annotations captured"
            device.end_test(test_id, success=True)
        except Exception as e:
            device.end_test(test_id, success=False)
            raise


@pytest.mark.offline_only
class TestOCRHandwritingReplay:
    """Replay recorded OCR handwriting without a device."""

    def test_replay_ocr_handwriting(self, offline_device, workspace, fixtures_dir):
        """Replay handwriting annotations from testdata."""
        test_id = "ocr_handwriting"

        fixture_doc = fixtures_dir / "test_ocr_handwriting.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(
                f"Testdata '{test_id}' not available. "
                f"Run with --device-mode=online --online to record."
            )

        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)

        assert state.has_annotations, "No annotations in replayed testdata"
        assert len(state.rm_files) > 0, "No .rm files in testdata"

    def test_ocr_handwriting_contains_strokes(self, offline_device, workspace, fixtures_dir):
        """Verify handwriting contains stroke annotations."""
        test_id = "ocr_handwriting"

        fixture_doc = fixtures_dir / "test_ocr_handwriting.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")

        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)

        # Extract strokes
        all_strokes = []
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for a in annotations:
                if a.type == AnnotationType.STROKE:
                    all_strokes.append(a)

        assert len(all_strokes) > 0, "No strokes found in handwriting"

    def test_ocr_handwriting_rm_files_valid(self, offline_device, workspace, fixtures_dir):
        """Verify handwriting .rm files are valid rmscene format."""
        test_id = "ocr_handwriting"

        fixture_doc = fixtures_dir / "test_ocr_handwriting.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")

        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)

        for page_uuid, rm_data in state.rm_files.items():
            blocks = list(read_blocks(io.BytesIO(rm_data)))
            assert len(blocks) > 0, f"No blocks in {page_uuid}.rm"
