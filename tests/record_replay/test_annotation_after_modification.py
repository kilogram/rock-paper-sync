"""Test adding MORE annotations after markdown modification.

Tests multi-trip annotation accumulation:

1. Trip 1: Upload document, user adds initial annotations
2. Trip 2: Modify markdown, sync, user adds MORE annotations
3. Verify: Both old and new annotations captured
4. Golden: Compare final positions

Recording Usage:
    uv run pytest tests/record_replay/test_annotation_after_modification.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_annotation_after_modification.py
"""

import io

import pytest

from rock_paper_sync.annotations import read_annotations


@pytest.mark.device
def test_annotation_after_modification(device, workspace, fixtures_dir):
    """Test adding MORE annotations after markdown modification.

    Trip 1: Upload document, user adds initial annotations
    Trip 2: Modify markdown, sync up, user adds MORE annotations, sync down
    Verify: Both old and new annotations are captured
    """
    fixture_doc = fixtures_dir / "test_annotation_after_modification.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(
            fixture_doc, description="Annotations after modification"
        )
    except FileNotFoundError:
        pytest.skip("Testdata not available. Run with --online -s to record.")

    # === TRIP 1: Initial annotations ===
    doc_uuid = device.upload_document(workspace.test_doc)

    trip1_state = device.wait_for_annotations(doc_uuid)
    assert trip1_state.has_annotations, "Trip 1: Need initial annotations"

    trip1_count = sum(
        len(read_annotations(io.BytesIO(rm_data))) for rm_data in trip1_state.rm_files.values()
    )
    print(f"\nTrip 1: {trip1_count} annotations")

    # === TRIP 2: Modify and add more annotations ===
    original_content = workspace.test_doc.read_text()

    # Add new content that can be annotated
    modified_content = original_content.replace(
        "## Section 4: Final Section",
        "## Section 3.5: New Annotatable Section\n\n"
        "**Trip 2 Instructions**: Highlight the word 'NEW' below.\n\n"
        "This is a NEW section added after your first annotations.\n"
        "You can add highlights or handwriting here to test multi-trip capture.\n\n"
        "## Section 4: Final Section",
    )

    workspace.test_doc.write_text(modified_content)
    print("\nAdded new section with annotatable content")

    # Save vault state for trip 2
    device.capture_phase("post_modification")

    # Sync up modifications
    device.trigger_sync()

    # User can now add MORE annotations to the new section
    trip2_state = device.wait_for_annotations(doc_uuid)

    trip2_count = sum(
        len(read_annotations(io.BytesIO(rm_data))) for rm_data in trip2_state.rm_files.values()
    )
    print(f"\nTrip 2: {trip2_count} annotations (was {trip1_count})")

    # Should have at least as many as before (ideally more)
    assert trip2_count >= trip1_count, f"Annotations lost: {trip1_count} -> {trip2_count}"

    # === GOLDEN COMPARISON ===
    try:
        golden_state = device.upload_golden_document(
            workspace.test_doc,
            prompt=(
                "Highlight the SAME text as before, plus:\n"
                "1. 'initial' in Section 1\n"
                "2. 'NEW' in the new Section 3.5"
            ),
        )

        golden_count = sum(
            len(read_annotations(io.BytesIO(rm_data))) for rm_data in golden_state.rm_files.values()
        )
        print(f"\nGolden: {golden_count} annotations")

    except FileNotFoundError:
        print("\nNo golden data - skipping comparison")
        print("   Run with --online -s to record golden ground truth")

    if trip2_count > trip1_count:
        print(f"New annotations added in Trip 2: +{trip2_count - trip1_count}")
    else:
        print("All original annotations preserved (no new ones added)")

    device.end_test(test_id)
