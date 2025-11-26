"""Tests for recording and replaying different pen tool annotations.

This test suite records annotations using different pen tools (ballpoint,
fineliner, marker, pencil, mechanical pencil, calligraphy) and verifies
that tool information is preserved through capture and replay.

Recording Usage:
    uv run pytest tests/record_replay/test_pen_tools.py \
        --device-mode=online --online

    When prompted, switch between different pen tools and write with each.

Replaying:
    uv run pytest tests/record_replay/test_pen_tools.py \
        --device-mode=offline
"""

import io
import pytest

from rmscene import read_blocks
from rmscene.scene_items import Pen
from rock_paper_sync.annotations import read_annotations, AnnotationType


@pytest.mark.device
@pytest.mark.online_only
class TestPenToolsRecording:
    """Record different pen tool annotations from a physical reMarkable device."""

    def test_record_pen_tools(self, device, workspace, fixtures_dir):
        """Record annotations using multiple pen tools.

        User Instructions:
        1. Select each pen tool from the menu:
           - Ballpoint
           - Fineliner
           - Marker
           - Pencil
           - Mechanical Pencil
           - Calligraphy
        2. Write the tool name with that tool
        3. Switch to next tool and repeat
        4. Try to use all 6 available tools
        5. Press Enter when done
        """
        test_id = "pen_tools"

        fixture_doc = fixtures_dir / "test_pen_tools.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        device.start_test(
            test_id,
            description="Annotations using different pen tools"
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
class TestPenToolsReplay:
    """Replay recorded pen tool annotations without a device."""

    def test_replay_pen_tools(self, offline_device, workspace, fixtures_dir):
        """Replay tool annotations from testdata."""
        test_id = "pen_tools"

        fixture_doc = fixtures_dir / "test_pen_tools.md"
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

    def test_pen_tools_multiple_tools(self, offline_device, workspace, fixtures_dir):
        """Verify multiple distinct pen tools are present in annotations."""
        test_id = "pen_tools"

        fixture_doc = fixtures_dir / "test_pen_tools.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")

        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)

        # Extract tools from strokes
        tools_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for a in annotations:
                if a.type == AnnotationType.STROKE:
                    tools_found.add(a.stroke.tool)

        # Should have multiple tools
        assert len(tools_found) >= 2, (
            f"Expected multiple tools, found {len(tools_found)}: {tools_found}"
        )

    def test_pen_tools_named(self, offline_device, workspace, fixtures_dir):
        """Verify tool names can be extracted from enum values."""
        test_id = "pen_tools"

        fixture_doc = fixtures_dir / "test_pen_tools.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")

        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)

        # Extract and name tools
        tools_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for a in annotations:
                if a.type == AnnotationType.STROKE:
                    tools_found.add(a.stroke.tool)

        # Map to names
        tool_names = [t.name for t in Pen if t.value in tools_found]
        assert len(tool_names) >= 2, f"Expected multiple tool names, found: {tool_names}"

    def test_pen_tools_ballpoint_present(self, offline_device, workspace, fixtures_dir):
        """Verify ballpoint tool is present (most commonly used)."""
        test_id = "pen_tools"

        fixture_doc = fixtures_dir / "test_pen_tools.md"
        workspace.test_doc.write_text(fixture_doc.read_text())

        try:
            offline_device.load_test(test_id)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{test_id}' not available")

        doc_uuid = offline_device.upload_document(workspace.test_doc)
        state = offline_device.wait_for_annotations(doc_uuid)

        # Extract tools
        tools_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for a in annotations:
                if a.type == AnnotationType.STROKE:
                    tools_found.add(a.stroke.tool)

        # Ballpoint is value 15
        assert Pen.BALLPOINT_2.value in tools_found, "Ballpoint tool not found"

    def test_pen_tools_rm_files_valid(self, offline_device, workspace, fixtures_dir):
        """Verify tool .rm files are valid rmscene format."""
        test_id = "pen_tools"

        fixture_doc = fixtures_dir / "test_pen_tools.md"
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
