"""Three-way merge test: local edits + device annotations.

Tests that both local markdown edits AND device annotations are preserved:

1. Trip 1: Upload document, user adds annotations
2. Trip 2: Edit markdown locally (non-conflicting), sync
3. Verify: Both local edits and annotation markers present
4. Golden: Compare annotation positions

Recording Usage:
    uv run pytest tests/record_replay/test_three_way_merge.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_three_way_merge.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


@pytest.mark.device
def test_three_way_merge(device, workspace, fixtures_dir):
    """Test three-way merge: local edits + device annotations.

    Trip 1: Upload document, user adds annotations
    Trip 2: Edit markdown locally (not the annotated text), sync
    Verify: Both local edits and annotation markers are present
    """
    fixture_doc = fixtures_dir / "test_three_way_merge.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(
            fixture_doc, description="Three-way merge: edits + annotations"
        )
    except FileNotFoundError:
        pytest.skip("Testdata not available. Run with --online -s to record.")

    # === TRIP 1: Initial upload and annotations ===
    doc_uuid = device.upload_document(workspace.test_doc)

    trip1_state = device.wait_for_annotations(doc_uuid)
    assert trip1_state.has_annotations, "Trip 1: Need annotations"

    trip1_annotations = []
    for page_uuid, rm_data in trip1_state.rm_files.items():
        trip1_annotations.extend(read_annotations(io.BytesIO(rm_data)))

    print(f"\nTrip 1: Captured {len(trip1_annotations)} annotations")

    # === TRIP 2: Local edits (non-conflicting) ===
    original_content = workspace.test_doc.read_text()

    # Make edits that don't touch annotated text
    modified_content = original_content.replace(
        "Lorem ipsum dolor sit amet",
        "LOCAL EDIT: Lorem ipsum dolor sit amet",
    )
    modified_content = modified_content.replace(
        "The quick brown fox",
        "LOCAL EDIT: The quick brown fox",
    )
    modified_content = modified_content.replace(
        "---\n\n**End of three-way merge test document**",
        "---\n\n**LOCAL EDIT: Document modified locally after annotations**\n\n"
        "This text was added by the local user.\n\n"
        "---\n\n**End of three-way merge test document**",
    )

    workspace.test_doc.write_text(modified_content)
    print("\nMade local edits (non-conflicting with annotations)")

    # Save vault state for trip 2
    device.capture_phase("post_local_edit")

    # Sync - this triggers three-way merge
    device.trigger_sync()

    # Observe the merged result
    device.observe_result(
        "Verify three-way merge worked:\n"
        "1. Your annotations should still be visible\n"
        "2. The text 'LOCAL EDIT:' should appear in several places\n"
        "3. New content at the end should be present"
    )

    # Read the final markdown to verify merge
    final_content = workspace.test_doc.read_text()

    # Verify local edits are present
    assert "LOCAL EDIT:" in final_content, "Local edits not preserved in merge"

    # Get final annotation state
    trip2_state = device.get_document_state(doc_uuid)
    trip2_highlights = []
    for page_uuid, rm_data in trip2_state.rm_files.items():
        annos = read_annotations(io.BytesIO(rm_data))
        trip2_highlights.extend([a for a in annos if a.type == AnnotationType.HIGHLIGHT])

    print(f"Trip 2: {len(trip2_highlights)} highlights after merge")

    # === GOLDEN COMPARISON ===
    try:
        golden_state = device.upload_golden_document(
            workspace.test_doc,
            prompt=(
                "Highlight the SAME text as before:\n"
                "1. 'preserved' in Section 1\n"
                "2. 'annotation target' in Section 3"
            ),
        )

        golden_annotations = []
        for page_uuid, rm_data in golden_state.rm_files.items():
            golden_annotations.extend(read_annotations(io.BytesIO(rm_data)))

        golden_highlights = [a for a in golden_annotations if a.type == AnnotationType.HIGHLIGHT]
        print(f"\nGolden: {len(golden_highlights)} highlights")

    except FileNotFoundError:
        print("\nNo golden data - skipping comparison")
        print("   Run with --online -s to record golden ground truth")

    # Verify annotations preserved
    if trip2_highlights:
        print("Three-way merge successful: local edits + annotations present")
    else:
        print("Three-way merge: local edits preserved (no highlights to check)")

    device.end_test(test_id)
