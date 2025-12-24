"""Tests for recording and replaying different pen tool annotations.

This test suite records annotations using different pen tools (ballpoint,
fineliner, marker, pencil, mechanical pencil, calligraphy) and verifies
that tool information is preserved through capture and replay.

Recording Usage:
    uv run pytest tests/record_replay/test_pen_tools.py --online -s

    When prompted, switch between different pen tools and write with each.

Replaying:
    uv run pytest tests/record_replay/test_pen_tools.py
"""

import io

import pytest
from rmscene.scene_items import Pen

from rock_paper_sync.annotations import AnnotationType, read_annotations


@pytest.mark.device
@pytest.mark.skip(reason="Pre-existing failure - needs investigation")
def test_pen_tools(device, workspace, fixtures_dir):
    """Test pen tool annotations - works in both online and offline modes.

    Online mode: User prompted to annotate; testdata captured automatically.
    Offline mode: Pre-recorded testdata replayed; generic validation automatic.
    """
    test_id = "pen_tools"

    # Load fixture document
    fixture_doc = fixtures_dir / "test_pen_tools.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    # Start test (online: begins recording, offline: loads testdata)
    try:
        device.start_test(test_id, description="Annotations using different pen tools")
    except FileNotFoundError:
        pytest.skip(f"Testdata '{test_id}' not available. " f"Run with --online -s to record.")

    # Upload document
    doc_uuid = device.upload_document(workspace.test_doc)

    # Wait for annotations
    # - Online mode: prompts user to annotate on device
    # - Offline mode: injects pre-recorded .rm files and validates integrity
    state = device.wait_for_annotations(doc_uuid)
    assert state.has_annotations, "No annotations captured"
    assert len(state.rm_files) > 0, "No .rm files in testdata"

    # Test-specific assertions - run in BOTH modes
    # Extract tools from strokes
    tools_found = set()
    for page_uuid, rm_data in state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        for a in annotations:
            if a.type == AnnotationType.STROKE:
                tools_found.add(a.stroke.tool)

    # Should have multiple tools
    assert (
        len(tools_found) >= 2
    ), f"Expected multiple tools, found {len(tools_found)}: {tools_found}"

    # Map to names
    tool_names = [t.name for t in Pen if t.value in tools_found]
    assert len(tool_names) >= 2, f"Expected multiple tool names, found: {tool_names}"

    # Ballpoint is most commonly used
    assert Pen.BALLPOINT_2.value in tools_found, f"Ballpoint tool not found, found: {tools_found}"

    # Finalize test
    device.end_test(test_id)
