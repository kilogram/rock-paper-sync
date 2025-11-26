"""Tests for recording and replaying pen width (thickness) variations.

This test suite records annotations with different pen widths and verifies
that thickness information is preserved through capture and replay.

Recording Usage:
    uv run pytest tests/record_replay/test_pen_widths.py \
        --device-mode=online --online

    When prompted, draw lines with varying pressure/widths.

Replaying:
    uv run pytest tests/record_replay/test_pen_widths.py \
        --device-mode=offline
"""

import io
import pytest

from rmscene import read_blocks
from rock_paper_sync.annotations import read_annotations, AnnotationType


@pytest.mark.device
@pytest.mark.online_only
class TestPenWidthsRecording:
    """Record pen width variations from a physical reMarkable device."""

    def test_record_pen_widths(self, device, workspace, fixtures_dir):
        """Record annotations with varying pen widths.

        User Instructions:
        1. Draw thin lines (light pressure)
        2. Draw medium-width lines (normal pressure)
        3. Draw thick lines (heavy pressure)
        4. Try variable pressure (start thin, get thick, end thin)
        5. Press Enter when done

        Tip: On reMarkable, pressure affects stroke thickness.
        Try different pressures to create varying widths.
        """
        test_id = "pen_widths"

        fixture_doc = fixtures_dir / "test_pen_widths.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        device.start_test(
            test_id,
            description="Annotations with varying pen widths"
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
class TestPenWidthsReplay:
    """Replay recorded pen width variations without a device."""

    def test_replay_pen_widths(self, offline_device, workspace, fixtures_dir):
        """Replay width variations from testdata."""
        test_id = "pen_widths"

        fixture_doc = fixtures_dir / "test_pen_widths.md"
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

    def test_pen_widths_multiple_thicknesses(self, offline_device, workspace, fixtures_dir):
        """Verify multiple distinct thickness values are present."""
        test_id = "pen_widths"

        fixture_doc = fixtures_dir / "test_pen_widths.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")

        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)

        # Extract thicknesses from strokes
        thicknesses = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for a in annotations:
                if a.type == AnnotationType.STROKE:
                    thicknesses.add(a.stroke.thickness)

        # Should have multiple thicknesses
        assert len(thicknesses) >= 2, (
            f"Expected multiple thickness values, found {len(thicknesses)}: "
            f"{sorted(thicknesses)}"
        )

    def test_pen_widths_all_positive(self, offline_device, workspace, fixtures_dir):
        """Verify all thickness values are positive floats."""
        test_id = "pen_widths"

        fixture_doc = fixtures_dir / "test_pen_widths.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")

        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)

        # Verify all thicknesses are positive
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for a in annotations:
                if a.type == AnnotationType.STROKE:
                    assert a.stroke.thickness > 0, (
                        f"Thickness should be positive, got: {a.stroke.thickness}"
                    )
                    assert isinstance(a.stroke.thickness, float), (
                        f"Thickness should be float, got: {type(a.stroke.thickness)}"
                    )

    def test_pen_widths_rm_files_valid(self, offline_device, workspace, fixtures_dir):
        """Verify width .rm files are valid rmscene format."""
        test_id = "pen_widths"

        fixture_doc = fixtures_dir / "test_pen_widths.md"
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
