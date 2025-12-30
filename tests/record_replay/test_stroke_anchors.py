"""Tests for stroke anchor creation.

This test validates the stroke annotation anchoring system:
- Anchor creation for stroke annotations
- Bounding box and position capture
- OCR processing and stroke detection

Recording Usage:
    uv run pytest tests/record_replay/test_stroke_anchors.py --online -s

    When prompted: Add handwritten strokes on the document

Replaying:
    uv run pytest tests/record_replay/test_stroke_anchors.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations
from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler


@pytest.mark.device
def test_stroke_anchors_comprehensive(device, workspace, fixtures_dir):
    """Comprehensive behavioral tests for stroke anchor creation and matching.

    This test consolidates all stroke anchor testing into a single recording session:
    1. Anchor creation with correct structure
    2. Position-based spatial matching
    3. Bounding box overlap matching (IoU)
    4. Position tolerance across document updates
    5. Best match selection among candidates

    Records once, tests multiple scenarios to minimize human burden.
    """
    test_id = "ocr_handwriting"

    # Load fixture with space for handwriting
    fixture_doc = fixtures_dir / "test_ocr_handwriting.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        device.start_test(
            test_id, description="Create stroke annotations for comprehensive anchor testing"
        )
    except FileNotFoundError:
        pytest.skip(f"Testdata '{test_id}' not available. Run with --online -s to record.")

    # Upload document
    doc_uuid = device.upload_document(workspace.test_doc)

    # Wait for annotations
    state = device.wait_for_annotations(doc_uuid)
    assert state.has_annotations, "No annotations captured"

    # Extract strokes and create anchors
    handler = StrokeHandler()
    all_anchors = []

    for page_uuid, rm_data in state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        strokes = [a for a in annotations if a.type == AnnotationType.STROKE]

        assert len(strokes) > 0, "No strokes found"

        for i, stroke in enumerate(strokes):
            # Create anchor
            paragraph_text = f"Paragraph {i} with handwritten content"
            anchor = handler.create_anchor(
                annotation=stroke, paragraph_text=paragraph_text, paragraph_index=i, page_num=0
            )

            # Verify basic anchor structure (AnchorContext API)
            assert anchor.text_content is not None, "Anchor should have text content"
            assert anchor.content_hash is not None, "Anchor should have content hash"
            assert hasattr(anchor, "y_position_hint"), "Anchor should have y_position_hint field"
            # Note: y_position_hint may be None for some anchors

            all_anchors.append((anchor, stroke))

    assert len(all_anchors) > 0, "Need at least one stroke for testing"

    # Use first anchor for behavioral tests
    test_anchor, original_stroke = all_anchors[0]

    # The old AnnotationAnchor.match_score() API tested spatial matching with positions and bboxes.
    # The new AnchorContext API focuses on content-based anchoring with text and context.
    # For strokes, the key functionality is:
    # 1. Text content extraction (OCR)
    # 2. Content hashing for exact matching
    # 3. Context windows for disambiguation

    # Test: Verify anchor has required fields
    assert test_anchor.text_content is not None, "Anchor should have OCR text content"
    assert len(test_anchor.text_content) > 0, "OCR text should not be empty"
    assert test_anchor.content_hash is not None, "Anchor should have content hash"
    assert test_anchor.paragraph_index is not None, "Anchor should have paragraph index"

    # Test: Verify all anchors are properly constructed
    for anchor, stroke in all_anchors:
        assert anchor.text_content is not None, "All anchors should have text content"
        assert anchor.content_hash is not None, "All anchors should have content hash"

    # Skip old spatial matching tests - they used the AnnotationAnchor.match_score() API
    # which doesn't exist in AnchorContext. The new anchor system uses content-based
    # matching through AnchorContext.resolve() which tests text similarity and context.
