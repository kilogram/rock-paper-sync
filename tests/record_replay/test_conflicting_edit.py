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

    # === GOLDEN COMPARISON ===
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

        golden_highlights = []
        for rm_data in golden_state.rm_files.values():
            annos = read_annotations(io.BytesIO(rm_data))
            golden_highlights.extend([a for a in annos if a.type == AnnotationType.HIGHLIGHT])

        print(f"\nGolden: {len(golden_highlights)} highlights")

    except FileNotFoundError:
        print("\nNo golden data - skipping comparison")
        print("   Run with --online -s to record golden ground truth")

    # Report the result (don't assert exact match since behavior is experimental)
    if len(trip2_highlights) == len(trip1_highlights):
        print(f"All {len(trip2_highlights)} highlights preserved despite text changes")
    elif len(trip2_highlights) > 0:
        print(
            f"Partial preservation: {len(trip1_highlights)} -> {len(trip2_highlights)} highlights"
        )
    else:
        print("All highlights lost after conflicting edit")

    device.end_test(test_id)
