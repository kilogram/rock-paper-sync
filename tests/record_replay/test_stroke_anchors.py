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

from rock_paper_sync.annotations import read_annotations, AnnotationType
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
        device.start_test(test_id, description="Create stroke annotations for comprehensive anchor testing")
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
                annotation=stroke,
                paragraph_text=paragraph_text,
                paragraph_index=i,
                page_num=0
            )

            # Verify basic anchor structure
            assert anchor.annotation_type == "stroke"
            assert anchor.page is not None
            assert anchor.bbox is not None

            # Verify position
            assert isinstance(anchor.page.x, (int, float))
            assert isinstance(anchor.page.y, (int, float))

            # Verify bounding box dimensions are positive
            assert anchor.bbox.width > 0, f"Expected positive width, got {anchor.bbox.width}"
            assert anchor.bbox.height > 0, f"Expected positive height, got {anchor.bbox.height}"

            all_anchors.append((anchor, stroke))

    assert len(all_anchors) > 0, "Need at least one stroke for testing"

    # Use first anchor for behavioral tests
    test_anchor, original_stroke = all_anchors[0]
    original_bbox = original_stroke.stroke.bounding_box

    # Test 1: Exact Position Match
    # Stroke at same position should score high
    exact_score = test_anchor.match_score(
        position=(test_anchor.page.x, test_anchor.page.y),
        bbox=(test_anchor.bbox.x, test_anchor.bbox.y, test_anchor.bbox.width, test_anchor.bbox.height)
    )
    assert exact_score > 0.7, \
        f"Exact position/bbox match should score high, got {exact_score:.2f}"

    # Test 2: Small Position Shift
    # Stroke moved slightly should still match (tolerance)
    small_shift = 10.0  # pixels
    shifted_score = test_anchor.match_score(
        position=(test_anchor.page.x + small_shift, test_anchor.page.y + small_shift),
        bbox=(test_anchor.bbox.x + small_shift, test_anchor.bbox.y + small_shift,
              test_anchor.bbox.width, test_anchor.bbox.height)
    )
    assert shifted_score > 0.6, \
        f"Small position shift should still match, got {shifted_score:.2f}"

    # Test 3: Bounding Box Resize
    # Slightly different bbox size should still match (partial overlap)
    resize_factor = 1.2
    resized_score = test_anchor.match_score(
        position=(test_anchor.page.x, test_anchor.page.y),
        bbox=(test_anchor.bbox.x, test_anchor.bbox.y,
              test_anchor.bbox.width * resize_factor, test_anchor.bbox.height * resize_factor)
    )
    assert resized_score > 0.4, \
        f"Resized bbox should partially match (IoU overlap), got {resized_score:.2f}"

    # Test 4: Large Position Shift - No Match
    # Stroke far away should not match
    large_shift = 500.0  # pixels
    distant_score = test_anchor.match_score(
        position=(test_anchor.page.x + large_shift, test_anchor.page.y + large_shift),
        bbox=(test_anchor.bbox.x + large_shift, test_anchor.bbox.y + large_shift,
              test_anchor.bbox.width, test_anchor.bbox.height)
    )
    assert distant_score < 0.3, \
        f"Distant stroke should not match, got {distant_score:.2f}"

    # Test 5: Position-Only Match
    # Position without bbox should still work (weighted lower)
    position_only_score = test_anchor.match_score(
        position=(test_anchor.page.x, test_anchor.page.y),
        bbox=None
    )
    assert position_only_score > 0.5, \
        f"Position-only match should work, got {position_only_score:.2f}"

    # Test 6: Bounding Box IoU
    # Test IoU calculation directly
    if test_anchor.bbox:
        # Same bbox = IoU of 1.0
        same_bbox_iou = test_anchor.bbox.iou(test_anchor.bbox)
        assert same_bbox_iou == 1.0, f"Same bbox should have IoU=1.0, got {same_bbox_iou:.2f}"

        # Non-overlapping bbox = IoU of 0.0
        from rock_paper_sync.annotations.common.anchors import BoundingBox
        distant_bbox = BoundingBox(
            x=test_anchor.bbox.x + 1000,
            y=test_anchor.bbox.y + 1000,
            width=test_anchor.bbox.width,
            height=test_anchor.bbox.height
        )
        no_overlap_iou = test_anchor.bbox.iou(distant_bbox)
        assert no_overlap_iou == 0.0, f"Non-overlapping bbox should have IoU=0.0, got {no_overlap_iou:.2f}"

    # Test 7: Best Match Selection
    # When multiple candidate positions, should pick the best match
    if len(all_anchors) >= 2:
        test_anchor2, _ = all_anchors[1]

        candidates = [
            # Candidate 1: Far away position
            (test_anchor.page.x + 500, test_anchor.page.y + 500),
            # Candidate 2: Exact match (should win)
            (test_anchor.page.x, test_anchor.page.y),
            # Candidate 3: Different anchor's position
            (test_anchor2.page.x, test_anchor2.page.y),
        ]

        scores = [
            test_anchor.match_score(position=pos, bbox=None)
            for pos in candidates
        ]

        best_index = scores.index(max(scores))
        assert best_index == 1, \
            f"Should identify exact position (index 1) as best match, got index {best_index} with scores {scores}"

    device.end_test(test_id)
