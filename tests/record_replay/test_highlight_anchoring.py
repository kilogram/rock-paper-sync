"""Highlight anchoring test for annotation re-positioning across modifications.

Tests that highlights re-anchor correctly when markdown content is modified:

1. Trip 1: Upload document, user highlights specific text
2. Trip 2: Insert text above/between highlights, sync
3. Verify: Highlights moved to follow their target text
4. Golden: Compare with device-native positions

Recording Usage:
    uv run pytest tests/record_replay/test_highlight_anchoring.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_highlight_anchoring.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


@pytest.mark.device
def test_highlight_anchoring_across_modifications(device, workspace, fixtures_dir):
    """Test that highlights re-anchor when markdown is modified.

    Trip 1: Upload document, user highlights text
    Trip 2: Insert text above highlights, sync, verify highlights moved correctly

    Uses golden data comparison to verify highlight positions match device-native
    behavior. Run with --online -s to record golden ground truth.
    """
    fixture_doc = fixtures_dir / "test_highlight_anchoring.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(
            fixture_doc, description="Highlight anchoring across modifications"
        )
    except FileNotFoundError:
        pytest.skip("Testdata not available. Run with --online -s to record.")

    # === TRIP 1: Initial upload and annotations ===
    doc_uuid = device.upload_document(workspace.test_doc)

    trip1_state = device.wait_for_annotations(doc_uuid)
    assert trip1_state.has_annotations, "Trip 1: Need annotations for this test"

    # Extract initial annotations
    trip1_annotations = []
    for page_uuid, rm_data in trip1_state.rm_files.items():
        trip1_annotations.extend(read_annotations(io.BytesIO(rm_data)))

    trip1_highlights = [a for a in trip1_annotations if a.type == AnnotationType.HIGHLIGHT]
    trip1_strokes = [a for a in trip1_annotations if a.type == AnnotationType.STROKE]
    assert len(trip1_highlights) >= 1, "Trip 1: Need at least one highlight"

    print(f"\nTrip 1: Captured {len(trip1_highlights)} highlights, {len(trip1_strokes)} strokes")

    # === TRIP 2: Modify markdown and sync ===
    original_content = workspace.test_doc.read_text()

    # Insert new content at the beginning (shifts all positions down)
    modified_content = original_content.replace(
        "# Highlight Anchoring Test",
        "# Highlight Anchoring Test\n\n"
        "> **INSERTED CONTENT**: This block was added after Trip 1 annotations.\n"
        "> It shifts all content below, testing position-independent anchoring.\n",
    )

    # Also insert content in the middle
    modified_content = modified_content.replace(
        "## Section 2: Shifting Content",
        "## Section 1.5: New Section (Inserted)\n\n"
        "This entire section was inserted after annotations were created.\n"
        "Content below should shift but annotations should follow their targets.\n\n"
        "## Section 2: Shifting Content",
    )

    workspace.test_doc.write_text(modified_content)
    print("\nModified markdown: inserted content at two locations")

    # Save vault state for trip 2
    device.capture_phase("post_modification")

    # Sync up the changes - this triggers re-anchoring
    device.trigger_sync()

    # Let user observe the result on device
    device.observe_result(
        "Check that your highlights moved correctly with the text.\n"
        "The document has new content at the top and middle.\n"
        "Your highlights should still be on the same TEXT, just at new positions."
    )

    # Get re-anchored state
    reanchored_state = device.get_document_state(doc_uuid)
    assert reanchored_state.has_annotations, "Trip 2: Annotations should persist"

    # Extract re-anchored annotations
    reanchored_annotations = []
    for page_uuid, rm_data in reanchored_state.rm_files.items():
        reanchored_annotations.extend(read_annotations(io.BytesIO(rm_data)))

    reanchored_highlights = [
        a for a in reanchored_annotations if a.type == AnnotationType.HIGHLIGHT
    ]
    reanchored_strokes = [a for a in reanchored_annotations if a.type == AnnotationType.STROKE]

    # === GOLDEN COMPARISON: Device-native ground truth ===
    golden_errors = []
    try:
        golden_state = device.upload_golden_document(
            workspace.test_doc,
            prompt=(
                "Highlight the SAME text as before:\n"
                "1. 'anchoring' in Section 1\n"
                "2. 'will shift down' in Section 2\n"
                "3. 'three-way merge' in Section 4\n"
                "Also add strokes near 'test' in Section 3 if possible."
            ),
        )

        # Extract golden annotations
        golden_annotations = []
        for page_uuid, rm_data in golden_state.rm_files.items():
            golden_annotations.extend(read_annotations(io.BytesIO(rm_data)))

        golden_highlights = [a for a in golden_annotations if a.type == AnnotationType.HIGHLIGHT]
        golden_strokes = [a for a in golden_annotations if a.type == AnnotationType.STROKE]

        # Compare highlight positions
        print("\nGolden comparison (highlights):")
        golden_errors.extend(
            _compare_highlight_positions(reanchored_highlights, golden_highlights, tolerance=50.0)
        )

        # Compare stroke counts
        print("\nGolden comparison (strokes):")
        print(f"   Re-anchored: {len(reanchored_strokes)} strokes")
        print(f"   Golden: {len(golden_strokes)} strokes")

    except FileNotFoundError:
        print("\nNo golden data - skipping position comparison")
        print("   Run with --online -s to record golden ground truth")

    # === Basic validation (always runs) ===
    assert (
        len(reanchored_highlights) == len(trip1_highlights)
    ), f"Highlights lost during modification: {len(trip1_highlights)} -> {len(reanchored_highlights)}"

    # Verify highlight TEXT content is preserved
    trip1_texts = {
        a.highlight.text.strip().lower()
        for a in trip1_highlights
        if a.highlight and a.highlight.text
    }
    reanchored_texts = {
        a.highlight.text.strip().lower()
        for a in reanchored_highlights
        if a.highlight and a.highlight.text
    }

    missing_texts = trip1_texts - reanchored_texts
    assert not missing_texts, f"Highlight texts changed. Missing: {missing_texts}"

    print(f"Trip 2: All {len(reanchored_highlights)} highlights preserved after modification")

    # Fail if golden comparison found issues
    if golden_errors:
        pytest.fail(
            f"Golden comparison failed with {len(golden_errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in golden_errors)
        )

    device.end_test(test_id)


def _compare_highlight_positions(
    reanchored: list, golden: list, tolerance: float = 50.0
) -> list[str]:
    """Compare highlight positions between re-anchored and golden annotations."""
    errors = []

    reanchored_by_text = {}
    for h in reanchored:
        if h.highlight and h.highlight.text and h.highlight.rectangles:
            text = h.highlight.text.strip().lower()
            reanchored_by_text[text] = h

    golden_by_text = {}
    for h in golden:
        if h.highlight and h.highlight.text and h.highlight.rectangles:
            text = h.highlight.text.strip().lower()
            golden_by_text[text] = h

    for text, golden_h in golden_by_text.items():
        if text not in reanchored_by_text:
            msg = f"Highlight '{text}' missing in re-anchored output"
            print(f"   - {msg}")
            errors.append(msg)
            continue

        reanchored_h = reanchored_by_text[text]
        golden_rect = golden_h.highlight.rectangles[0]
        reanchored_rect = reanchored_h.highlight.rectangles[0]

        y_diff = abs(reanchored_rect.y - golden_rect.y)
        status = "ok" if y_diff <= tolerance else "FAIL"

        print(
            f"   {status} '{text}': "
            f"reanchored y={reanchored_rect.y:.1f}, "
            f"golden y={golden_rect.y:.1f}, "
            f"diff={y_diff:.1f}px"
        )

        if y_diff > tolerance:
            msg = f"Highlight '{text}' position mismatch: diff={y_diff:.1f}px"
            errors.append(msg)

    return errors
