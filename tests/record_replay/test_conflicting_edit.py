"""Conflicting edit test: editing highlighted text.

Tests what happens when the EXACT text that was highlighted is modified:

1. Trip 1: Upload document, user highlights specific text
2. Trip 2: Edit the highlighted text itself, sync
3. Observe: How anchoring handles the conflict
4. Golden: Compare with device-native behavior

Recording Usage:
    uv run pytest tests/record_replay/test_conflicting_edit.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_conflicting_edit.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


@pytest.mark.device
def test_conflicting_edit_on_highlighted_text(device, workspace, fixtures_dir):
    """Test editing the exact text that was highlighted.

    Trip 1: Upload document, user highlights specific text
    Trip 2: Edit the highlighted text itself, sync, observe anchor behavior
    """
    fixture_doc = fixtures_dir / "test_conflicting_edit.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(
            fixture_doc, description="Conflicting edit on highlighted text"
        )
    except FileNotFoundError:
        pytest.skip("Testdata not available. Run with --online -s to record.")

    # === TRIP 1: Highlight specific text ===
    doc_uuid = device.upload_document(workspace.test_doc)

    trip1_state = device.wait_for_annotations(doc_uuid)
    assert trip1_state.has_annotations, "Trip 1: Need annotations"

    trip1_highlights = []
    for rm_data in trip1_state.rm_files.values():
        annos = read_annotations(io.BytesIO(rm_data))
        trip1_highlights.extend([a for a in annos if a.type == AnnotationType.HIGHLIGHT])

    print(f"\nTrip 1: {len(trip1_highlights)} highlights")

    # === TRIP 2: Edit the text that was highlighted ===
    original_content = workspace.test_doc.read_text()

    # Modify text that the user was instructed to highlight
    modified_content = original_content.replace(
        "annotation anchoring system",
        "MODIFIED annotation anchoring system",
    )
    modified_content = modified_content.replace(
        "will shift down",
        "WILL DEFINITELY SHIFT DOWN",
    )
    modified_content = modified_content.replace(
        "three-way merge algorithm",
        "THREE-WAY MERGE ALGORITHM",
    )

    workspace.test_doc.write_text(modified_content)
    print("\nModified text that was highlighted (changed case/words)")

    # Save vault state for trip 2
    device.capture_phase("post_conflicting_edit")

    # Sync
    device.trigger_sync()

    # Observe - user sees how anchoring handled the conflict
    device.observe_result(
        "CONFLICT TEST: The text you highlighted has been modified.\n"
        "Observe how the system handled this:\n"
        "1. Did highlights move to the modified text?\n"
        "2. Did highlights disappear?\n"
        "3. Did highlights stay on nearby similar text?\n"
        "Press Enter to continue..."
    )

    # Get final state
    trip2_state = device.get_document_state(doc_uuid)

    trip2_highlights = []
    for rm_data in trip2_state.rm_files.values():
        annos = read_annotations(io.BytesIO(rm_data))
        trip2_highlights.extend([a for a in annos if a.type == AnnotationType.HIGHLIGHT])

    # === VALIDATION: Check highlight rectangles are within page bounds ===
    # reMarkable uses CENTERED coordinates where 0 = page center
    # Page dimensions: ~1404 x 1872 pixels
    # So valid X range is approximately [-702, +702] (centered)
    # Y uses absolute coords: [0, 1872]
    PAGE_WIDTH = 1404
    PAGE_HEIGHT = 1872
    PAGE_CENTER_X = PAGE_WIDTH / 2  # 702
    BOUNDS_TOLERANCE = 100  # Allow some margin for edge cases

    print(f"\nTrip 2: {len(trip2_highlights)} highlights after modification")
    bounds_errors = []
    for i, h in enumerate(trip2_highlights):
        if h.highlight and h.highlight.rectangles:
            for j, rect in enumerate(h.highlight.rectangles):
                text_preview = h.highlight.text[:30] if h.highlight.text else "?"
                print(
                    f"  Highlight '{text_preview}' rect[{j}]: x={rect.x:.1f}, y={rect.y:.1f}, w={rect.w:.1f}, h={rect.h:.1f}"
                )

                # Check X bounds (centered coordinate system)
                if rect.x < -(PAGE_CENTER_X + BOUNDS_TOLERANCE) or rect.x > (
                    PAGE_CENTER_X + BOUNDS_TOLERANCE
                ):
                    bounds_errors.append(
                        f"Highlight {i} rect {j}: x={rect.x:.1f} outside centered page width [-{PAGE_CENTER_X}, +{PAGE_CENTER_X}]"
                    )
                # Check Y bounds (absolute, 0 = top of page)
                if rect.y < -BOUNDS_TOLERANCE or rect.y > PAGE_HEIGHT + BOUNDS_TOLERANCE:
                    bounds_errors.append(
                        f"Highlight {i} rect {j}: y={rect.y:.1f} outside page height [0, {PAGE_HEIGHT}]"
                    )

    # === GOLDEN COMPARISON ===
    golden_highlights = []
    try:
        golden_state = device.upload_golden_document(
            workspace.test_doc,
            prompt=(
                "Highlight the MODIFIED text:\n"
                "1. 'MODIFIED annotation anchoring system'\n"
                "2. 'WILL DEFINITELY SHIFT DOWN'\n"
                "3. 'THREE-WAY MERGE ALGORITHM'"
            ),
        )

        for rm_data in golden_state.rm_files.values():
            annos = read_annotations(io.BytesIO(rm_data))
            golden_highlights.extend([a for a in annos if a.type == AnnotationType.HIGHLIGHT])

        print(f"\nGolden: {len(golden_highlights)} highlights")

    except FileNotFoundError:
        print("\nNo golden data - skipping comparison")
        print("   Run with --online -s to record golden ground truth")

    # === ASSERTIONS ===
    # 1. All highlights should be preserved
    assert len(trip2_highlights) == len(trip1_highlights), (
        f"Highlights lost during modification: {len(trip1_highlights)} -> {len(trip2_highlights)}\n"
        f"Expected all {len(trip1_highlights)} highlights to be preserved."
    )
    print(f"✓ All {len(trip2_highlights)} highlights preserved")

    # 2. Highlight rectangles should be within page bounds
    assert not bounds_errors, "Highlight rectangles outside page bounds:\n" + "\n".join(
        f"  - {e}" for e in bounds_errors
    )
    print("✓ All highlight rectangles within page bounds")

    device.end_test(test_id)
