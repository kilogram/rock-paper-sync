"""Tests for recording and replaying highlight annotations.

This test suite records actual highlight annotations from a device
and verifies they are captured and replayed correctly.

Recording Usage:
    uv run pytest tests/record_replay/test_highlights.py --online -s

    When prompted, use the highlight tool to highlight text on your device.

Replaying:
    uv run pytest tests/record_replay/test_highlights.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


@pytest.mark.device
def test_highlights(device, workspace, fixtures_dir):
    """Test highlight annotations - works in both online and offline modes.

    Online mode: User prompted to annotate; testdata captured automatically.
    Offline mode: Pre-recorded testdata replayed; generic validation automatic.
    """
    test_id = "highlights"

    # Load fixture document
    fixture_doc = fixtures_dir / "test_highlights.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    # Start test (online: begins recording, offline: loads testdata)
    try:
        device.start_test(test_id, description="Highlight annotations with multiple colors")
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
    # Extract annotations
    all_annotations = []
    for page_uuid, rm_data in state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        all_annotations.extend(annotations)

    # Verify we have some annotations
    assert len(all_annotations) > 0, "No annotations extracted"

    # Count highlights
    highlight_count = sum(1 for a in all_annotations if a.type == AnnotationType.HIGHLIGHT)

    # Should have at least one highlight annotation
    assert highlight_count >= 1, f"Expected highlights, found {highlight_count}"

    # Finalize test
    device.end_test(test_id)
