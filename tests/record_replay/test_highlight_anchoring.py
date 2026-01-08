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

    # === VALIDATION: Check highlight rectangles are within page bounds ===
    # reMarkable uses CENTERED coordinates where 0 = page center
    # Page dimensions: ~1404 x 1872 pixels
    # So valid X range is approximately [-702, +702] (centered)
    # Y uses absolute coords: [0, 1872]
    page_width = 1404
    page_height = 1872
    page_center_x = page_width / 2  # 702
    bounds_tolerance = 100  # Allow some margin for edge cases

    print("\nRe-anchored highlight positions:")
    bounds_errors = []
    for i, h in enumerate(reanchored_highlights):
        if h.highlight and h.highlight.rectangles:
            for j, rect in enumerate(h.highlight.rectangles):
                text_preview = h.highlight.text[:30] if h.highlight.text else "?"
                print(
                    f"  '{text_preview}' rect[{j}]: "
                    f"x={rect.x:.1f}, y={rect.y:.1f}, w={rect.w:.1f}, h={rect.h:.1f}"
                )

                # Check X bounds (centered coordinate system)
                if rect.x < -(page_center_x + bounds_tolerance) or rect.x > (
                    page_center_x + bounds_tolerance
                ):
                    bounds_errors.append(
                        f"Highlight '{text_preview}' rect {j}: x={rect.x:.1f} outside centered page width [-{page_center_x}, +{page_center_x}]"
                    )
                # Check Y bounds (absolute, 0 = top of page)
                if rect.y < -bounds_tolerance or rect.y > page_height + bounds_tolerance:
                    bounds_errors.append(
                        f"Highlight '{text_preview}' rect {j}: y={rect.y:.1f} outside page height [0, {page_height}]"
                    )

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
        # X tolerance is tight (layout engine matches device well for horizontal positioning)
        # Y tolerance is loose (our line breaks differ from device, causing Y drift)
        print("\nGolden comparison (highlights):")
        golden_errors.extend(
            _compare_highlight_positions(
                reanchored_highlights,
                golden_highlights,
                x_tolerance=50.0,  # X positions should match closely
                y_tolerance=500.0,  # Y positions may differ due to layout engine differences
            )
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

    # === ASSERTIONS ===
    # 1. Highlight rectangles should be within page bounds
    assert not bounds_errors, "Highlight rectangles outside page bounds:\n" + "\n".join(
        f"  - {e}" for e in bounds_errors
    )
    print("✓ All highlight rectangles within page bounds")

    # 2. Fail if golden comparison found issues
    if golden_errors:
        pytest.fail(
            f"Golden comparison failed with {len(golden_errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in golden_errors)
        )

    device.end_test(test_id)


def _compare_highlight_positions(
    reanchored: list,
    golden: list,
    x_tolerance: float = 50.0,
    y_tolerance: float = 50.0,
) -> list[str]:
    """Compare highlight positions between re-anchored and golden annotations.

    Args:
        reanchored: Re-anchored highlights from sync
        golden: Golden highlights from device-native recording
        x_tolerance: Tolerance for X position comparison (tight - layout matches well)
        y_tolerance: Tolerance for Y position comparison (loose - line breaks may differ)

    Returns:
        List of error messages for mismatches
    """
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

        x_diff = abs(reanchored_rect.x - golden_rect.x)
        y_diff = abs(reanchored_rect.y - golden_rect.y)
        x_ok = x_diff <= x_tolerance
        y_ok = y_diff <= y_tolerance
        status = "ok" if x_ok and y_ok else "FAIL"

        print(
            f"   {status} '{text}': "
            f"reanchored ({reanchored_rect.x:.1f}, {reanchored_rect.y:.1f}), "
            f"golden ({golden_rect.x:.1f}, {golden_rect.y:.1f}), "
            f"diff=({x_diff:.1f}, {y_diff:.1f})px"
        )

        if not x_ok:
            msg = f"Highlight '{text}' X position mismatch: diff={x_diff:.1f}px"
            errors.append(msg)
        if not y_ok:
            msg = f"Highlight '{text}' Y position mismatch: diff={y_diff:.1f}px"
            errors.append(msg)

    return errors
