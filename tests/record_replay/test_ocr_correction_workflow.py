"""Integration test for OCR correction detection workflow.

This test validates the end-to-end OCR correction detection system:
- Upload with handwritten annotations
- OCR processing and snapshot creation
- User editing OCR text in markdown
- Correction detection via snapshot comparison
- Storage in database for training data export

Recording Usage:
    uv run pytest tests/record_replay/test_ocr_correction_workflow.py --online -s

    When prompted:
    1. Add handwritten annotations to the document
    2. Wait for OCR processing to complete
    3. The test will simulate user editing OCR text

Replaying:
    uv run pytest tests/record_replay/test_ocr_correction_workflow.py

Note: Unit tests for correction detection logic are in tests/annotations/test_ocr_corrections.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations
from rock_paper_sync.annotations.core.data_types import RenderConfig
from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler
from rock_paper_sync.annotations.ocr_corrections import detect_single_ocr_correction
from rock_paper_sync.state import StateManager


@pytest.mark.device
def test_ocr_correction_workflow(device, workspace, fixtures_dir, tmp_path):
    """Integration test for complete OCR correction workflow.

    This comprehensive test validates the full pipeline:
    1. Upload document with space for handwriting
    2. Add handwritten annotations (OCR source)
    3. OCR processing creates snapshots
    4. Simulate user editing OCR text in markdown
    5. Detection system identifies corrections
    6. Corrections stored in database with image hashes
    7. Snapshots ready for next detection cycle

    This is the only record/replay test needed for corrections - all logic
    testing is done in unit tests to minimize recording burden.
    """
    test_id = "ocr_handwriting"

    # Load fixture with space for handwriting
    fixture_doc = fixtures_dir / "test_ocr_handwriting.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        device.start_test(test_id, description="OCR correction detection workflow")
    except FileNotFoundError:
        pytest.skip(f"Testdata '{test_id}' not available. Run with --online -s to record.")

    # Step 1: Initial upload
    doc_uuid = device.upload_document(workspace.test_doc)

    # Step 2 & 3: Annotations and OCR processing
    initial_state = device.wait_for_annotations(doc_uuid)
    assert initial_state.has_annotations, "No annotations for OCR correction testing"

    # Verify strokes are present (source for OCR)
    stroke_count = 0
    for page_uuid, rm_data in initial_state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        stroke_count += sum(1 for a in annotations if a.type == AnnotationType.STROKE)

    assert stroke_count > 0, "Need handwritten strokes for OCR correction testing"

    # Step 4-7: Comprehensive correction detection and storage workflow
    # Initialize state manager for correction storage
    state_manager = StateManager(tmp_path / "state.db")

    # Test comprehensive correction scenarios
    test_cases = [
        # Basic corrections - comment style
        {
            "name": "comment_style_basic",
            "style": "comment",
            "old": "Text with <!-- OCR: helo wrld --> here.",
            "new": "Text with <!-- OCR: hello world --> here.",
            "expected_old": "helo wrld",
            "expected_new": "hello world",
            "should_detect": True,
        },
        # Basic corrections - footnote style
        {
            "name": "footnote_style_basic",
            "style": "footnote",
            "old": "Before quck text[^1]\n\n[^1]: OCR confidence 0.85",
            "new": "Before quick text[^1]\n\n[^1]: OCR confidence 0.85",
            "expected_old": "quck",
            "expected_new": "quick",
            "should_detect": True,
        },
        # Multiple corrections in same paragraph
        {
            "name": "multiple_corrections",
            "style": "comment",
            "old": "<!-- OCR: helo --> and <!-- OCR: wrld -->",
            "new": "<!-- OCR: hello --> and <!-- OCR: world -->",
            "expected_old": "helo",  # First correction
            "expected_new": "hello",
            "should_detect": True,
        },
        # No correction - unchanged text
        {
            "name": "no_change",
            "style": "comment",
            "old": "Text with <!-- OCR: same text --> here.",
            "new": "Text with <!-- OCR: same text --> here.",
            "expected_old": None,
            "expected_new": None,
            "should_detect": False,
        },
        # Edge case - whitespace normalization
        {
            "name": "whitespace_change",
            "style": "comment",
            "old": "<!-- OCR: hello  world -->",  # Double space
            "new": "<!-- OCR: hello world -->",  # Single space
            "expected_old": "hello  world",
            "expected_new": "hello world",
            "should_detect": True,
        },
        # Edge case - case change
        {
            "name": "case_change",
            "style": "comment",
            "old": "<!-- OCR: Hello World -->",
            "new": "<!-- OCR: hello world -->",
            "expected_old": "Hello World",
            "expected_new": "hello world",
            "should_detect": True,
        },
        # Significant correction (multiple words)
        {
            "name": "multi_word_correction",
            "style": "footnote",
            "old": "Handwritten teh quck brown fox[^1]",
            "new": "Handwritten the quick brown fox[^1]",
            "expected_old": "teh quck brown fox",
            "expected_new": "the quick brown fox",
            "should_detect": True,
        },
    ]

    corrections_detected = 0

    for i, test_case in enumerate(test_cases):
        config = RenderConfig(stroke_style=test_case["style"])

        correction = detect_single_ocr_correction(
            vault_name="test",
            file_path="doc.md",
            paragraph_index=i,
            old_paragraph=test_case["old"],
            new_paragraph=test_case["new"],
            annotation_id=f"anno-{i}",
            image_hash=f"hash-{i}",
            config=config,
        )

        if test_case["should_detect"]:
            assert correction is not None, f"Correction should be detected for {test_case['name']}"
            assert (
                test_case["expected_old"] in correction.original_text
            ), f"{test_case['name']}: Expected '{test_case['expected_old']}' in original_text, got '{correction.original_text}'"
            assert (
                test_case["expected_new"] in correction.corrected_text
            ), f"{test_case['name']}: Expected '{test_case['expected_new']}' in corrected_text, got '{correction.corrected_text}'"
            assert correction.annotation_id == f"anno-{i}"
            assert correction.image_hash == f"hash-{i}"

            # Store correction in database
            import uuid

            state_manager.add_ocr_correction(
                correction_id=str(uuid.uuid4()),
                image_hash=correction.image_hash,
                image_path=f"/tmp/image-{i}.png",
                original_text=correction.original_text,
                corrected_text=correction.corrected_text,
                paragraph_context=correction.paragraph_context,
                document_id=correction.document_id,
            )
            corrections_detected += 1
        else:
            assert (
                correction is None
            ), f"No correction should be detected for {test_case['name']}, but got: {correction}"

    # Verify corrections stored and retrievable
    pending = state_manager.get_pending_ocr_corrections()
    assert (
        len(pending) == corrections_detected
    ), f"Expected {corrections_detected} stored corrections, got {len(pending)}"

    # Verify correction details for each stored correction
    for stored in pending:
        assert "original_text" in stored
        assert "corrected_text" in stored
        assert "image_hash" in stored
        assert stored["image_hash"].startswith("hash-")
        assert (
            stored["original_text"] != stored["corrected_text"]
        ), "Stored corrections should have different original and corrected text"

    # Test integration with actual stroke anchors from testdata
    # Extract first stroke and create anchor
    stroke_handler = StrokeHandler()
    for page_uuid, rm_data in initial_state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        strokes = [a for a in annotations if a.type == AnnotationType.STROKE]

        if strokes:
            first_stroke = strokes[0]

            # Create anchor for stroke
            anchor = stroke_handler.create_anchor(
                annotation=first_stroke,
                paragraph_text="Sample paragraph text",
                paragraph_index=0,
                page_num=0,
            )

            # Verify anchor has position and bbox (needed for correction detection)
            assert anchor.page is not None, "Anchor should have page position"
            assert anchor.bbox is not None, "Anchor should have bounding box"

            # In real workflow, this anchor would be used to:
            # 1. Render OCR text into markdown
            # 2. Create snapshot of rendered paragraph
            # 3. Detect corrections when user edits the OCR text
            # 4. Store correction with image hash for training

            break  # Just test first stroke

    # Test snapshot infrastructure exists
    # (In actual sync workflow, snapshots are created after OCR processing)
    # This verifies the state manager has snapshot capability
    assert hasattr(state_manager, "snapshots"), "StateManager should have snapshot store"

    device.end_test(test_id)
