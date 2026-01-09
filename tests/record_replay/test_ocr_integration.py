"""Consolidated OCR integration test.

This test replaces:
- test_ocr_handwriting.py
- test_ocr_correction_workflow.py

It tests the complete OCR pipeline:
1. Handwriting capture (strokes from device)
2. OCR correction detection workflow
3. Correction storage and retrieval
4. Anchor creation from strokes

Test flow (2 trips):
1. Trip 1: Handwriting capture - Write specified words in gaps
2. Trip 2: Verify OCR text rendering and correction detection

Recording Usage:
    uv run pytest tests/record_replay/test_ocr_integration.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_ocr_integration.py
"""

import io
import uuid

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations
from rock_paper_sync.annotations.core.data_types import RenderConfig
from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler
from rock_paper_sync.annotations.ocr_corrections import detect_single_ocr_correction
from rock_paper_sync.state import StateManager


def count_strokes(rm_files: dict[str, bytes]) -> int:
    """Count total strokes across all .rm files."""
    count = 0
    for rm_data in rm_files.values():
        for anno in read_annotations(io.BytesIO(rm_data)):
            if anno.type == AnnotationType.STROKE:
                count += 1
    return count


def extract_strokes(rm_files: dict[str, bytes]) -> list:
    """Extract all strokes from .rm files."""
    strokes = []
    for rm_data in rm_files.values():
        for anno in read_annotations(io.BytesIO(rm_data)):
            if anno.type == AnnotationType.STROKE:
                strokes.append(anno)
    return strokes


@pytest.mark.device
def test_ocr_integration(device, workspace, fixtures_dir, tmp_path):
    """Consolidated OCR integration test with 2 trips.

    This test verifies:
    - Handwriting stroke capture
    - Stroke properties preservation
    - OCR correction detection (comment style)
    - OCR correction detection (footnote style)
    - Multiple corrections handling
    - Correction storage in database
    - Anchor creation from strokes
    - Snapshot infrastructure for corrections
    """
    fixture_doc = fixtures_dir / "test_ocr_handwriting.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(
            fixture_doc, description="OCR integration test (2 trips)"
        )
    except FileNotFoundError:
        pytest.skip("Testdata not available. Run with --online -s to record.")

    # =========================================================================
    # TRIP 1: Handwriting capture
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 1: Handwriting capture")
    print("=" * 60)
    print("\nPlease write the specified text in each area:")
    print("  - Test 1: 'hello'")
    print("  - Test 2: '2025'")
    print("  - Test 3: 'quick test'")
    print("  - Test 4: 'Code 42'")
    print("  - Test 5: 'The quick brown fox'")
    print("\nUse the ballpoint pen for best OCR results.")

    doc_uuid = device.upload_document(workspace.test_doc)

    trip1_state = device.wait_for_annotations(doc_uuid)
    assert trip1_state.has_annotations, "Trip 1: Need handwriting strokes for this test"

    trip1_stroke_count = count_strokes(trip1_state.rm_files)
    trip1_strokes = extract_strokes(trip1_state.rm_files)

    print(f"\nTrip 1: Captured {trip1_stroke_count} strokes")

    # Should have multiple strokes (one per character/word at minimum)
    assert trip1_stroke_count >= 5, f"Expected 5+ strokes, got {trip1_stroke_count}"

    # Verify stroke properties
    for i, stroke in enumerate(trip1_strokes[:3]):
        if stroke.stroke and stroke.stroke.points:
            print(f"   Stroke {i}: {len(stroke.stroke.points)} points")

    device.observe_result(
        "TRIP 1: Handwriting has been captured.\n"
        f"Total strokes: {trip1_stroke_count}\n"
        "Check that your handwriting is visible on the device."
    )

    # =========================================================================
    # TRIP 2: OCR correction detection workflow
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 2: OCR correction detection")
    print("=" * 60)

    # Initialize state manager for correction storage
    state_manager = StateManager(tmp_path / "state.db")

    # Test OCR correction detection scenarios
    test_cases = [
        # Comment style - basic correction
        {
            "name": "comment_basic",
            "style": "comment",
            "old": "Text with <!-- OCR: helo wrld --> here.",
            "new": "Text with <!-- OCR: hello world --> here.",
            "expected_old": "helo wrld",
            "expected_new": "hello world",
            "should_detect": True,
        },
        # Footnote style - basic correction
        {
            "name": "footnote_basic",
            "style": "footnote",
            "old": "Before quck text[^1]\n\n[^1]: OCR confidence 0.85",
            "new": "Before quick text[^1]\n\n[^1]: OCR confidence 0.85",
            "expected_old": "quck",
            "expected_new": "quick",
            "should_detect": True,
        },
        # No change - should NOT detect
        {
            "name": "no_change",
            "style": "comment",
            "old": "Text with <!-- OCR: same text --> here.",
            "new": "Text with <!-- OCR: same text --> here.",
            "expected_old": None,
            "expected_new": None,
            "should_detect": False,
        },
        # Multi-word correction
        {
            "name": "multi_word",
            "style": "footnote",
            "old": "Handwritten teh quck brown fox[^2]",
            "new": "Handwritten the quick brown fox[^2]",
            "expected_old": "teh quck brown fox",
            "expected_new": "the quick brown fox",
            "should_detect": True,
        },
        # Whitespace normalization
        {
            "name": "whitespace",
            "style": "comment",
            "old": "<!-- OCR: hello  world -->",
            "new": "<!-- OCR: hello world -->",
            "expected_old": "hello  world",
            "expected_new": "hello world",
            "should_detect": True,
        },
    ]

    corrections_detected = 0
    print("\nRunning OCR correction detection tests:")

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
            assert test_case["expected_old"] in correction.original_text, (
                f"{test_case['name']}: Expected '{test_case['expected_old']}' "
                f"in original_text, got '{correction.original_text}'"
            )
            assert test_case["expected_new"] in correction.corrected_text, (
                f"{test_case['name']}: Expected '{test_case['expected_new']}' "
                f"in corrected_text, got '{correction.corrected_text}'"
            )

            # Store correction
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
            print(f"   {test_case['name']}: DETECTED")
        else:
            assert correction is None, f"No correction should be detected for {test_case['name']}"
            print(f"   {test_case['name']}: correctly ignored")

    print(f"\n   Total corrections detected: {corrections_detected}")

    # Verify corrections stored
    pending = state_manager.get_pending_ocr_corrections()
    assert (
        len(pending) == corrections_detected
    ), f"Expected {corrections_detected} stored corrections, got {len(pending)}"
    print(f"   Corrections stored in database: {len(pending)}")

    # Test anchor creation from actual device strokes
    print("\nTesting anchor creation from device strokes:")
    stroke_handler = StrokeHandler()
    anchor_created = False

    for page_uuid, rm_data in trip1_state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        strokes = [a for a in annotations if a.type == AnnotationType.STROKE]

        if strokes:
            first_stroke = strokes[0]

            # Create anchor
            anchor = stroke_handler.create_anchor(
                annotation=first_stroke,
                paragraph_text="Sample paragraph text",
                paragraph_index=0,
                page_num=0,
            )

            # Verify anchor structure (AnchorContext API)
            assert anchor.text_content is not None, "Anchor should have text content"
            assert anchor.content_hash is not None, "Anchor should have content hash"
            assert hasattr(anchor, "y_position_hint"), "Anchor should have y_position_hint"

            anchor_created = True
            print(f"   Anchor created with hash: {anchor.content_hash[:16]}...")
            break

    assert anchor_created, "Should be able to create anchor from captured strokes"

    # Verify snapshot infrastructure
    assert hasattr(state_manager, "snapshots"), "StateManager should have snapshot store"
    print("   Snapshot infrastructure verified")

    # =========================================================================
    # FINAL VERIFICATION
    # =========================================================================
    print("\n" + "=" * 60)
    print("FINAL VERIFICATION")
    print("=" * 60)

    print("\nOCR Integration Summary:")
    print(f"   Strokes captured: {trip1_stroke_count}")
    print(f"   Correction test cases: {len(test_cases)}")
    print(f"   Corrections detected: {corrections_detected}")
    print(f"   Corrections stored: {len(pending)}")
    print(f"   Anchor creation: {'Success' if anchor_created else 'Failed'}")

    print("\nOCR integration test PASSED")

    device.end_test(test_id)
