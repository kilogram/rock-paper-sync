"""Tests for recording and replaying pen width (thickness) variations.

This test suite records annotations with different pen widths and verifies
that thickness information is preserved through capture and replay.

Recording Usage:
    uv run pytest tests/record_replay/test_pen_widths.py --online -s

    When prompted, draw lines with varying pressure/widths.

Replaying:
    uv run pytest tests/record_replay/test_pen_widths.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


@pytest.mark.device
def test_pen_widths(device, workspace, fixtures_dir):
    """Test pen width variations - works in both online and offline modes.

    Online mode: User prompted to annotate; testdata captured automatically.
    Offline mode: Pre-recorded testdata replayed; generic validation automatic.
    """
    test_id = "pen_widths"

    # Load fixture document
    fixture_doc = fixtures_dir / "test_pen_widths.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    # Start test (online: begins recording, offline: loads testdata)
    try:
        device.start_test(test_id, description="Annotations with varying pen widths")
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
    # Extract thicknesses from strokes
    thicknesses = set()
    for page_uuid, rm_data in state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        for a in annotations:
            if a.type == AnnotationType.STROKE:
                thicknesses.add(a.stroke.thickness)

                # Verify all thicknesses are positive floats
                assert (
                    a.stroke.thickness > 0
                ), f"Thickness should be positive, got: {a.stroke.thickness}"
                assert isinstance(
                    a.stroke.thickness, float
                ), f"Thickness should be float, got: {type(a.stroke.thickness)}"

    # Should have multiple thickness values
    assert len(thicknesses) >= 2, (
        f"Expected multiple thickness values, found {len(thicknesses)}: " f"{sorted(thicknesses)}"
    )

    # Finalize test
    device.end_test(test_id)
