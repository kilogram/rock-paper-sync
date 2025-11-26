"""Tests for recording and replaying pen color annotations.

This test suite records annotations in different colors and verifies
that color information is preserved through capture and replay.

Recording Usage:
    uv run pytest tests/record_replay/test_pen_colors.py --online -s

    When prompted, use different pen colors to write text.

Replaying:
    uv run pytest tests/record_replay/test_pen_colors.py
"""

import io
import pytest

from rmscene.scene_items import PenColor
from rock_paper_sync.annotations import read_annotations, AnnotationType


@pytest.mark.device
def test_pen_colors(device, workspace, fixtures_dir):
    """Test pen color annotations - works in both online and offline modes.

    Online mode: User prompted to annotate; testdata captured automatically.
    Offline mode: Pre-recorded testdata replayed; generic validation automatic.
    """
    test_id = "pen_colors"

    # Load fixture document
    fixture_doc = fixtures_dir / "test_pen_colors.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    # Start test (online: begins recording, offline: loads testdata)
    try:
        device.start_test(test_id, description="Annotations with multiple pen colors")
    except FileNotFoundError:
        pytest.skip(
            f"Testdata '{test_id}' not available. "
            f"Run with --online -s to record."
        )

    # Upload document
    doc_uuid = device.upload_document(workspace.test_doc)

    # Wait for annotations
    # - Online mode: prompts user to annotate on device
    # - Offline mode: injects pre-recorded .rm files and validates integrity
    state = device.wait_for_annotations(doc_uuid)
    assert state.has_annotations, "No annotations captured"
    assert len(state.rm_files) > 0, "No .rm files in testdata"

    # Test-specific assertions - run in BOTH modes
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

    # Black should be present
    assert PenColor.BLACK.value in colors_found, (
        f"Expected black color to be present, found: {colors_found}"
    )

    # Finalize test
    device.end_test(test_id)
