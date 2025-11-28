"""Tests for recording and replaying OCR handwriting annotations.

This test suite records handwritten text from a device and verifies
the OCR system can recognize and process the handwriting.

Recording Usage:
    uv run pytest tests/record_replay/test_ocr_handwriting.py --online -s

    When prompted, use the ballpoint pen to write the specified words.

Replaying:
    uv run pytest tests/record_replay/test_ocr_handwriting.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


@pytest.mark.device
def test_ocr_handwriting(device, workspace, fixtures_dir):
    """Test OCR handwriting annotations - works in both online and offline modes.

    Online mode: User prompted to annotate; testdata captured automatically.
    Offline mode: Pre-recorded testdata replayed; generic validation automatic.
    """
    test_id = "ocr_handwriting"

    # Load fixture document
    fixture_doc = fixtures_dir / "test_ocr_handwriting.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    # Start test (online: begins recording, offline: loads testdata)
    try:
        device.start_test(test_id, description="OCR handwriting samples")
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
    # Extract strokes (handwriting should produce strokes)
    all_strokes = []
    for page_uuid, rm_data in state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        for a in annotations:
            if a.type == AnnotationType.STROKE:
                all_strokes.append(a)

    assert len(all_strokes) > 0, "No strokes found in handwriting"

    # Finalize test
    device.end_test(test_id)
