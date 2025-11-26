"""Tests for recording and replaying pen color annotations.

This test suite records annotations in different colors and verifies
that color information is preserved through capture and replay.

Recording Usage:
    uv run pytest tests/record_replay/test_pen_colors.py \
        --device-mode=online --online

    When prompted, use different pen colors to write text.

Replaying:
    uv run pytest tests/record_replay/test_pen_colors.py \
        --device-mode=offline
"""

import io
import pytest

from rmscene import read_blocks
from rmscene.scene_items import PenColor
from rock_paper_sync.annotations import read_annotations, AnnotationType


@pytest.mark.device
@pytest.mark.online_only
class TestPenColorsRecording:
    """Record pen color annotations from a physical reMarkable device."""

    def test_record_pen_colors(self, device, workspace, fixtures_dir):
        """Record annotations using multiple pen colors.

        User Instructions:
        1. Select a pen color
        2. Write the color name (e.g., "black", "red", "blue")
        3. Switch colors and repeat
        4. Try to use at least 3-4 different colors
        5. Press Enter when done

        Colors available:
        - Black (default)
        - Red
        - Blue
        - Green
        - Yellow
        - Pink/Purple
        """
        test_id = "pen_colors"

        fixture_doc = fixtures_dir / "test_pen_colors.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        device.start_test(
            test_id,
            description="Annotations with multiple pen colors"
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
class TestPenColorsReplay:
    """Replay recorded pen color annotations without a device."""

    def test_replay_pen_colors(self, offline_device, workspace, fixtures_dir):
        """Replay color annotations from testdata."""
        test_id = "pen_colors"

        fixture_doc = fixtures_dir / "test_pen_colors.md"
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

    def test_pen_colors_multiple_colors(self, offline_device, workspace, fixtures_dir):
        """Verify multiple distinct colors are present in annotations."""
        test_id = "pen_colors"

        fixture_doc = fixtures_dir / "test_pen_colors.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")

        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)

        # Extract colors from strokes
        colors_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for a in annotations:
                if a.type == AnnotationType.STROKE:
                    colors_found.add(a.stroke.color)

        # Should have multiple colors
        assert len(colors_found) >= 2, (
            f"Expected multiple colors, found {len(colors_found)}: {colors_found}"
        )

    def test_pen_colors_black_preserved(self, offline_device, workspace, fixtures_dir):
        """Verify black color is preserved in annotations."""
        test_id = "pen_colors"

        fixture_doc = fixtures_dir / "test_pen_colors.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")

        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)

        # Extract strokes
        colors_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for a in annotations:
                if a.type == AnnotationType.STROKE:
                    colors_found.add(a.stroke.color)

        # Black should be present
        assert PenColor.BLACK.value in colors_found

    def test_pen_colors_rm_files_valid(self, offline_device, workspace, fixtures_dir):
        """Verify color .rm files are valid rmscene format."""
        test_id = "pen_colors"

        fixture_doc = fixtures_dir / "test_pen_colors.md"
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
