"""Multi-trip sync tests for annotation anchoring and three-way merge.

These tests verify that annotations survive markdown modifications across
multiple sync round-trips:

1. Trip 1: Upload → user annotates → sync down annotations
2. Trip 2: Modify markdown locally → sync up → observe on device → sync down
3. Verify: Annotations re-anchored correctly, three-way merge worked

Recording Usage:
    uv run pytest tests/record_replay/test_multi_trip.py --online -s

    Follow the prompts for each trip. You'll annotate, then observe
    the result of markdown modifications on your device.

Replaying:
    uv run pytest tests/record_replay/test_multi_trip.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


@pytest.mark.device
def test_highlight_anchoring_across_modifications(device, workspace, fixtures_dir):
    """Test that highlights re-anchor when markdown is modified.

    Trip 1: Upload document, user highlights text
    Trip 2: Insert text above highlights, sync, verify highlights moved correctly
    """
    fixture_doc = fixtures_dir / "test_multi_trip.md"
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
    assert len(trip1_highlights) >= 1, "Trip 1: Need at least one highlight"

    print(f"\n📊 Trip 1: Captured {len(trip1_highlights)} highlights")

    # === TRIP 2: Modify markdown and sync ===
    original_content = workspace.test_doc.read_text()

    # Insert new content at the beginning (shifts all positions down)
    modified_content = original_content.replace(
        "# Multi-Trip Annotation Test",
        "# Multi-Trip Annotation Test\n\n"
        "> **INSERTED CONTENT**: This block was added after Trip 1 annotations.\n"
        "> It shifts all content below, testing position-independent anchoring.\n",
    )

    # Also insert content in the middle
    modified_content = modified_content.replace(
        "## Section 2: Text That Will Shift",
        "## Section 1.5: New Section (Inserted)\n\n"
        "This entire section was inserted after annotations were created.\n"
        "Content below should shift but annotations should follow their targets.\n\n"
        "## Section 2: Text That Will Shift",
    )

    workspace.test_doc.write_text(modified_content)
    print("\n✏️  Modified markdown: inserted ~300 chars at two locations")

    # Sync up the changes
    device.trigger_sync()

    # Let user observe the result on device
    device.observe_result(
        "Check that your highlights moved correctly with the text.\n"
        "The document has new content at the top and middle.\n"
        "Your highlights should still be on the same TEXT, just at new positions."
    )

    # Get final state
    trip2_state = device.get_document_state(doc_uuid)
    assert trip2_state.has_annotations, "Trip 2: Annotations should persist"

    # Extract final annotations
    trip2_annotations = []
    for page_uuid, rm_data in trip2_state.rm_files.items():
        trip2_annotations.extend(read_annotations(io.BytesIO(rm_data)))

    trip2_highlights = [a for a in trip2_annotations if a.type == AnnotationType.HIGHLIGHT]

    # Verify no highlights were lost
    assert len(trip2_highlights) == len(
        trip1_highlights
    ), f"Highlights lost during modification: {len(trip1_highlights)} -> {len(trip2_highlights)}"

    print(f"✅ Trip 2: All {len(trip2_highlights)} highlights preserved after modification")

    device.end_test(test_id)


@pytest.mark.device
def test_three_way_merge(device, workspace, fixtures_dir):
    """Test three-way merge: local edits + device annotations.

    Trip 1: Upload document, user adds annotations
    Trip 2: Edit markdown locally (not the annotated text), sync
    Verify: Both local edits and annotation markers are present
    """
    fixture_doc = fixtures_dir / "test_multi_trip.md"
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

    print(f"\n📊 Trip 1: Captured {len(trip1_annotations)} annotations")

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
        "---\n\n**End of multi-trip test document**",
        "---\n\n**LOCAL EDIT: Document modified locally after annotations**\n\n"
        "This text was added by the local user.\n\n"
        "---\n\n**End of multi-trip test document**",
    )

    workspace.test_doc.write_text(modified_content)
    print("\n✏️  Made local edits (non-conflicting with annotations)")

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

    # Verify annotation markers are present (if highlights were added)
    trip2_state = device.get_document_state(doc_uuid)
    trip2_highlights = []
    for page_uuid, rm_data in trip2_state.rm_files.items():
        annos = read_annotations(io.BytesIO(rm_data))
        trip2_highlights.extend([a for a in annos if a.type == AnnotationType.HIGHLIGHT])

    if trip2_highlights:
        # Check for annotation markers in merged content
        has_markers = "<!-- ANNOTATED:" in final_content or "ANNOTATED:" in final_content
        assert has_markers, (
            "Three-way merge failed: annotation markers not found.\n"
            f"Local edits present: {'LOCAL EDIT:' in final_content}\n"
            f"Highlights count: {len(trip2_highlights)}"
        )
        print("✅ Three-way merge successful: local edits + annotation markers present")
    else:
        print("✅ Three-way merge successful: local edits preserved (no highlights to mark)")

    device.end_test(test_id)


@pytest.mark.device
def test_annotation_after_modification(device, workspace, fixtures_dir):
    """Test adding MORE annotations after markdown modification.

    Trip 1: Upload document, user adds initial annotations
    Trip 2: Modify markdown, sync up, user adds MORE annotations, sync down
    Verify: Both old and new annotations are captured
    """
    fixture_doc = fixtures_dir / "test_multi_trip.md"
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
    print(f"\n📊 Trip 1: {trip1_count} annotations")

    # === TRIP 2: Modify and add more annotations ===
    original_content = workspace.test_doc.read_text()

    # Add new content that can be annotated
    modified_content = original_content.replace(
        "## Section 6: Final Section",
        "## Section 5.5: New Annotatable Section\n\n"
        "**Instructions for Trip 2**: Highlight the word 'NEW' below.\n\n"
        "This is a NEW section added after your first annotations.\n"
        "You can add highlights or handwriting here to test multi-trip capture.\n\n"
        "## Section 6: Final Section",
    )

    workspace.test_doc.write_text(modified_content)
    print("\n✏️  Added new section with annotatable content")

    # Sync up modifications
    device.trigger_sync()

    # User can now add MORE annotations to the new section
    trip2_state = device.wait_for_annotations(doc_uuid)

    trip2_count = sum(
        len(read_annotations(io.BytesIO(rm_data))) for rm_data in trip2_state.rm_files.values()
    )
    print(f"\n📊 Trip 2: {trip2_count} annotations (was {trip1_count})")

    # Should have at least as many as before (ideally more)
    assert trip2_count >= trip1_count, f"Annotations lost: {trip1_count} -> {trip2_count}"

    if trip2_count > trip1_count:
        print(f"✅ New annotations added in Trip 2: +{trip2_count - trip1_count}")
    else:
        print("✅ All original annotations preserved (no new ones added)")

    device.end_test(test_id)


@pytest.mark.device
def test_conflicting_edit_on_highlighted_text(device, workspace, fixtures_dir):
    """Test editing the exact text that was highlighted.

    Trip 1: Upload document, user highlights specific text
    Trip 2: Edit the highlighted text itself, sync, observe anchor behavior
    """
    fixture_doc = fixtures_dir / "test_multi_trip.md"
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

    print(f"\n📊 Trip 1: {len(trip1_highlights)} highlights")

    # === TRIP 2: Edit the text that was likely highlighted ===
    original_content = workspace.test_doc.read_text()

    # Modify text that the user was instructed to highlight
    modified_content = original_content.replace(
        "The annotation anchoring system",
        "The MODIFIED annotation anchoring system",
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
    print("\n✏️  Modified text that was likely highlighted (changed case/words)")

    # Sync
    device.trigger_sync()

    # Observe - this is the key: user sees how anchoring handled the conflict
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

    # We don't assert exact match since behavior depends on anchor matching
    # Just report the result
    if len(trip2_highlights) == len(trip1_highlights):
        print(f"✅ All {len(trip2_highlights)} highlights preserved despite text changes")
    elif len(trip2_highlights) > 0:
        print(
            f"⚠️  Partial preservation: {len(trip1_highlights)} -> {len(trip2_highlights)} highlights"
        )
    else:
        print("❌ All highlights lost after conflicting edit")

    device.end_test(test_id)
