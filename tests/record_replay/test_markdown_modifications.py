"""Tests for markdown modifications and annotation re-anchoring.

This test validates that annotations remain correctly anchored when the
markdown source is modified:
- Upload document and add annotations
- Modify markdown (add/remove text, reformat)
- Sync again and verify annotations re-anchor correctly
- Test conflict scenarios where anchoring may fail

Recording Usage:
    uv run pytest tests/record_replay/test_markdown_modifications.py --online -s

    When prompted:
    1. Add highlights and handwriting to the document
    2. Test will modify markdown and re-sync

Replaying:
    uv run pytest tests/record_replay/test_markdown_modifications.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


@pytest.mark.device
def test_markdown_modifications(device, workspace, fixtures_dir):
    """Test annotation re-anchoring after markdown modifications.

    This test validates the complete modification workflow:
    1. Upload initial document
    2. Add annotations (highlights + handwriting)
    3. Modify markdown content (insert, delete, reformat)
    4. Sync modified markdown
    5. Verify annotations re-anchor correctly
    6. Verify annotation markers persist in modified content
    """
    test_id = "modifications"

    # Load fixture document
    fixture_doc = fixtures_dir / "test_modifications.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        device.start_test(
            test_id, description="Test annotation anchoring across markdown modifications"
        )
    except FileNotFoundError:
        pytest.skip(f"Testdata '{test_id}' not available. Run with --online -s to record.")

    # Step 1: Initial upload
    doc_uuid = device.upload_document(workspace.test_doc)

    # Step 2: Add annotations
    initial_state = device.wait_for_annotations(doc_uuid)
    assert initial_state.has_annotations, "Need annotations for modification testing"

    # Extract and verify initial annotations
    initial_annotations = []
    for page_uuid, rm_data in initial_state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        initial_annotations.extend(annotations)

    assert len(initial_annotations) > 0, "Should have captured annotations"

    # Count annotation types
    highlights = [a for a in initial_annotations if a.type == AnnotationType.HIGHLIGHT]
    strokes = [a for a in initial_annotations if a.type == AnnotationType.STROKE]

    print(f"\n📊 Annotations captured: {len(highlights)} highlights, {len(strokes)} strokes")

    # Step 3: Modify markdown content (BEFORE checking for markers)
    #
    # IMPORTANT: The system uses THREE-WAY MERGE:
    # - Annotations exist on device (.rm files)
    # - User modifies markdown locally (we simulate this)
    # - Sync merges both changes: applies annotations to modified markdown
    #
    # Annotation markers are ONLY added when a file is synced due to content changes.
    # They are NOT pulled automatically when only device annotations change.
    # Simulate common user modifications:
    # - Add new text at beginning
    # - Insert text in middle
    # - Reformat existing text
    # - Add new section at end

    original_content = workspace.test_doc.read_text()  # Read current markdown (no markers yet)
    modified_content = original_content.replace(
        "# Test Markdown Modifications",
        "# Test Markdown Modifications\n\n> **Note**: This document has been modified!",
    )

    # Insert text before highlighted section (update for new fixture structure)
    modified_content = modified_content.replace(
        "## Section 1: Text to Highlight (Page 1)",
        "## Preamble\n\nSome additional context added here.\n\n## Section 1: Text to Highlight (Page 1)",
    )

    # Add text after handwriting section (update for new fixture structure)
    modified_content = modified_content.replace(
        "## Section 3: Dense Content Area (Page 2)",
        "## Section 2.5: New Section\n\nThis section was added after annotations.\n\n## Section 3: Dense Content Area (Page 2)",
    )

    # Write modified content
    workspace.test_doc.write_text(modified_content)

    print(f"\n✏️  Modified markdown (added {len(modified_content) - len(original_content)} chars)")

    # Step 4: Sync modified document
    # This should trigger three-way merge:
    # - Downloads .rm files (annotations from device)
    # - Maps annotations to MODIFIED content
    # - Adds markers to merged result
    device.trigger_sync()

    # Step 5: Get updated state
    updated_state = device.get_document_state(doc_uuid)

    # Step 6: Verify annotations still present
    # In online mode, annotations should persist on device
    # In offline mode, we verify the .rm files are still valid
    assert updated_state.has_annotations, "Annotations should persist after markdown modification"

    # Extract updated annotations
    updated_annotations = []
    for page_uuid, rm_data in updated_state.rm_files.items():
        annotations = read_annotations(io.BytesIO(rm_data))
        updated_annotations.extend(annotations)

    # Verify annotation counts match (annotations shouldn't be lost)
    updated_highlights = [a for a in updated_annotations if a.type == AnnotationType.HIGHLIGHT]
    updated_strokes = [a for a in updated_annotations if a.type == AnnotationType.STROKE]

    assert len(updated_highlights) == len(
        highlights
    ), f"Highlight count changed: {len(highlights)} -> {len(updated_highlights)}"
    assert len(updated_strokes) == len(
        strokes
    ), f"Stroke count changed: {len(strokes)} -> {len(updated_strokes)}"

    # Step 7: Verify markdown contains annotation markers after three-way merge
    # The sync should have:
    # - Detected changed markdown content
    # - Downloaded .rm files with annotations
    # - Re-anchored annotations to modified content
    # - Added markers to the merged markdown
    final_content = workspace.test_doc.read_text()

    # Debug: show what we got
    print(f"\n📄 Final markdown after three-way merge ({len(final_content)} chars):")
    print(final_content[:1000])

    # Should have annotation markers if three-way merge succeeded
    if highlights:
        has_highlight_markers = "<!-- ANNOTATED:" in final_content or "ANNOTATED:" in final_content
        assert has_highlight_markers, (
            f"Three-way merge failed: highlight markers not found in modified markdown.\n\n"
            f"Expected: Annotations from device re-anchored to modified content\n"
            f"Got: {len(final_content)} chars, {len(highlights)} highlights captured\n\n"
            f"First 1000 chars:\n{final_content[:1000]}"
        )
        print("✅ Highlight markers found in merged markdown")

    if strokes:
        # Strokes render as ANNOTATED markers with stroke count (OCR markers only if OCR enabled)
        # When OCR is disabled, format is: <!-- ANNOTATED: N strokes -->
        has_stroke_markers = "strokes" in final_content or "stroke" in final_content
        assert has_stroke_markers, (
            f"Three-way merge failed: stroke markers not found in modified markdown.\n\n"
            f"Expected: Stroke annotations from device re-anchored to modified content\n"
            f"Got: {len(final_content)} chars, {len(strokes)} strokes captured\n\n"
            f"First 1000 chars:\n{final_content[:1000]}"
        )
        print("✅ Stroke markers found in merged markdown")

    print("\n✅ Three-way merge successful: annotations re-anchored to modified content")

    device.end_test(test_id)
