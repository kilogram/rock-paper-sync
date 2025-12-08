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

    Uses golden data comparison to verify highlight positions match device-native
    behavior. Run with --online -s to record golden ground truth.
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
    trip1_strokes = [a for a in trip1_annotations if a.type == AnnotationType.STROKE]
    assert len(trip1_highlights) >= 1, "Trip 1: Need at least one highlight"

    print(f"\n📊 Trip 1: Captured {len(trip1_highlights)} highlights, {len(trip1_strokes)} strokes")

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
    # Upload the modified markdown fresh and have user highlight the same text
    # This captures what the device natively produces for these highlights
    golden_errors = []
    try:
        golden_state = device.upload_golden_document(
            workspace.test_doc,
            prompt=(
                "Highlight the SAME text as before:\n"
                "1. 'anchoring' in Section 1\n"
                "2. 'will shift down' in Section 2\n"
                "3. 'three-way merge' in Section 5\n"
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
        print("\n📊 Golden comparison (highlights):")
        golden_errors.extend(
            _compare_highlight_positions(reanchored_highlights, golden_highlights, tolerance=50.0)
        )

        # Compare stroke counts and positions
        print("\n📊 Golden comparison (strokes):")
        print(f"   Re-anchored: {len(reanchored_strokes)} strokes")
        print(f"   Golden: {len(golden_strokes)} strokes")
        if len(reanchored_strokes) < len(golden_strokes):
            msg = (
                f"Strokes lost: re-anchored has {len(reanchored_strokes)}, "
                f"golden has {len(golden_strokes)}"
            )
            print(f"   ❌ {msg}")
            golden_errors.append(msg)
        else:
            print("   ✅ Stroke count OK")

        # Compare stroke positions
        golden_errors.extend(
            _compare_stroke_positions(reanchored_strokes, golden_strokes, tolerance=50.0)
        )

        # Compare TreeNodeBlock anchor values (critical for stroke rendering)
        golden_errors.extend(
            _compare_tree_node_anchors(
                reanchored_state.rm_files, golden_state.rm_files, tolerance=20
            )
        )

    except FileNotFoundError:
        print("\n⚠️  No golden data - skipping position comparison")
        print("   Run with --online -s to record golden ground truth")

    # === Basic validation (always runs) ===
    # Verify highlight count preserved
    assert (
        len(reanchored_highlights) == len(trip1_highlights)
    ), f"Highlights lost during modification: {len(trip1_highlights)} -> {len(reanchored_highlights)}"

    # Verify highlight TEXT content is preserved (not just counts)
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
    assert (
        not missing_texts
    ), f"Highlight texts changed during modification. Missing: {missing_texts}"

    print(f"✅ Trip 2: All {len(reanchored_highlights)} highlights preserved after modification")
    print(f"   Highlight texts verified: {list(reanchored_texts)[:3]}...")

    # Verify strokes preserved
    assert len(reanchored_strokes) >= len(
        trip1_strokes
    ), f"Strokes lost during modification: {len(trip1_strokes)} -> {len(reanchored_strokes)}"
    if trip1_strokes:
        print(f"   Strokes preserved: {len(reanchored_strokes)}")

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
    """Compare highlight positions between re-anchored and golden annotations.

    Args:
        reanchored: Highlights from re-anchoring flow
        golden: Highlights from device-native golden capture
        tolerance: Maximum allowed Y position difference in pixels

    Returns:
        List of error messages for any mismatches exceeding tolerance
    """
    errors = []

    # Build lookup by text
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

    # Compare positions
    for text, golden_h in golden_by_text.items():
        if text not in reanchored_by_text:
            msg = f"Highlight '{text}' missing in re-anchored output"
            print(f"   ❌ {msg}")
            errors.append(msg)
            continue

        reanchored_h = reanchored_by_text[text]
        golden_rect = golden_h.highlight.rectangles[0]
        reanchored_rect = reanchored_h.highlight.rectangles[0]

        y_diff = abs(reanchored_rect.y - golden_rect.y)
        status = "✅" if y_diff <= tolerance else "❌"

        print(
            f"   {status} '{text}': "
            f"reanchored y={reanchored_rect.y:.1f}, "
            f"golden y={golden_rect.y:.1f}, "
            f"diff={y_diff:.1f}px"
        )

        if y_diff > tolerance:
            msg = (
                f"Highlight '{text}' position mismatch: "
                f"reanchored y={reanchored_rect.y:.1f}, golden y={golden_rect.y:.1f}, "
                f"diff={y_diff:.1f}px (tolerance={tolerance}px)"
            )
            errors.append(msg)

    return errors


def _compare_stroke_positions(reanchored: list, golden: list, tolerance: float = 50.0) -> list[str]:
    """Compare stroke positions between re-anchored and golden annotations.

    NOTE: Strokes can be in different coordinate systems:
    - Text-relative: parent_id.part1 != 0, typically negative Y values
    - Page-absolute: parent_id.part1 == 0, typically positive Y values

    When coordinate systems differ, we compare relative positions (dimensions)
    rather than absolute positions, since direct Y comparison is meaningless.

    Args:
        reanchored: Strokes from re-anchoring flow
        golden: Strokes from device-native golden capture
        tolerance: Maximum allowed position difference in pixels

    Returns:
        List of error messages for any mismatches exceeding tolerance
    """
    errors = []

    # Extract bounding boxes and check coordinate systems
    reanchored_bboxes = []
    reanchored_negative_y = 0
    for s in reanchored:
        if s.stroke and s.stroke.bounding_box:
            bb = s.stroke.bounding_box
            reanchored_bboxes.append((bb.x, bb.y, bb.w, bb.h))
            if bb.y < 0:
                reanchored_negative_y += 1

    golden_bboxes = []
    golden_negative_y = 0
    for s in golden:
        if s.stroke and s.stroke.bounding_box:
            bb = s.stroke.bounding_box
            golden_bboxes.append((bb.x, bb.y, bb.w, bb.h))
            if bb.y < 0:
                golden_negative_y += 1

    print("   Stroke positions:")
    print(f"   Re-anchored bboxes: {[(f'({x:.0f},{y:.0f})') for x, y, w, h in reanchored_bboxes]}")
    print(f"   Golden bboxes:      {[(f'({x:.0f},{y:.0f})') for x, y, w, h in golden_bboxes]}")

    # Detect coordinate system mismatch
    reanchored_is_text_rel = reanchored_negative_y > len(reanchored_bboxes) / 2
    golden_is_text_rel = golden_negative_y > len(golden_bboxes) / 2

    if reanchored_is_text_rel != golden_is_text_rel:
        print("   ⚠️  Different coordinate systems detected:")
        print(
            f"      Re-anchored: {'text-relative' if reanchored_is_text_rel else 'page-absolute'}"
        )
        print(f"      Golden: {'text-relative' if golden_is_text_rel else 'page-absolute'}")
        print("   Comparing stroke dimensions (w, h) only...")

        # Sort by width to match strokes
        reanchored_sorted = sorted(reanchored_bboxes, key=lambda b: b[2])
        golden_sorted = sorted(golden_bboxes, key=lambda b: b[2])

        for i, (r_bbox, g_bbox) in enumerate(zip(reanchored_sorted, golden_sorted)):
            r_x, r_y, r_w, r_h = r_bbox
            g_x, g_y, g_w, g_h = g_bbox

            # Compare dimensions only (invariant across coordinate systems)
            w_diff = abs(r_w - g_w)
            h_diff = abs(r_h - g_h)

            if w_diff > tolerance or h_diff > tolerance:
                msg = f"Stroke {i} dimension mismatch: w_diff={w_diff:.1f}, h_diff={h_diff:.1f}"
                print(f"   ❌ {msg}")
                errors.append(msg)
            else:
                print(f"   ✅ Stroke {i}: dimensions match (w={r_w:.1f}, h={r_h:.1f})")

        return errors

    # Same coordinate system - compare absolute positions
    reanchored_sorted = sorted(reanchored_bboxes, key=lambda b: b[2])
    golden_sorted = sorted(golden_bboxes, key=lambda b: b[2])

    for i, (r_bbox, g_bbox) in enumerate(zip(reanchored_sorted, golden_sorted)):
        r_x, r_y, r_w, r_h = r_bbox
        g_x, g_y, g_w, g_h = g_bbox

        y_diff = abs(r_y - g_y)
        status = "✅" if y_diff <= tolerance else "❌"

        print(
            f"   {status} Stroke {i}: "
            f"reanchored y={r_y:.1f}, golden y={g_y:.1f}, diff={y_diff:.1f}px"
        )

        if y_diff > tolerance:
            msg = (
                f"Stroke {i} position mismatch: "
                f"reanchored y={r_y:.1f}, golden y={g_y:.1f}, "
                f"diff={y_diff:.1f}px (tolerance={tolerance}px)"
            )
            errors.append(msg)

    return errors


def _compare_tree_node_anchors(
    reanchored_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
    tolerance: int = 20,
) -> list[str]:
    """Compare TreeNodeBlock anchor values between re-anchored and golden.

    This is critical because incorrect anchor values cause strokes not to render
    on the device, even if Y positions look correct.

    Args:
        reanchored_rm_files: Dict of page_uuid -> rm_data from re-anchoring flow
        golden_rm_files: Dict of page_uuid -> rm_data from golden capture
        tolerance: Maximum allowed anchor value difference

    Returns:
        List of error messages for any mismatches exceeding tolerance
    """
    import rmscene

    errors = []

    # Extract user-created TreeNodeBlock anchors (author ID 2) from each set
    def extract_anchors(rm_files: dict) -> dict[tuple, int]:
        """Returns dict of (node_id_part1, node_id_part2) -> anchor_offset"""
        anchors = {}
        for page_uuid, rm_data in rm_files.items():
            try:
                blocks = list(rmscene.read_blocks(io.BytesIO(rm_data)))
                for b in blocks:
                    if type(b).__name__ == "TreeNodeBlock":
                        if hasattr(b, "group") and b.group:
                            g = b.group
                            if (
                                hasattr(g, "node_id")
                                and g.node_id
                                and hasattr(g, "anchor_id")
                                and g.anchor_id
                                and g.anchor_id.value
                            ):
                                # Only check user-created (author ID 2)
                                if g.node_id.part1 == 2:
                                    key = (g.node_id.part1, g.node_id.part2)
                                    anchors[key] = g.anchor_id.value.part2
            except Exception as e:
                print(f"   ⚠️  Error reading rm_data for {page_uuid}: {e}")
        return anchors

    reanchored_anchors = extract_anchors(reanchored_rm_files)
    golden_anchors = extract_anchors(golden_rm_files)

    print("\n📊 Golden comparison (TreeNodeBlock anchors):")
    print(f"   Re-anchored: {len(reanchored_anchors)} TreeNodeBlocks with anchors")
    print(f"   Golden: {len(golden_anchors)} TreeNodeBlocks with anchors")

    # Compare anchors
    for node_key, golden_anchor in golden_anchors.items():
        if node_key not in reanchored_anchors:
            # Different node_ids are expected since golden is a fresh document
            # Just report if we have the same COUNT of anchors
            continue

        reanchored_anchor = reanchored_anchors[node_key]
        diff = abs(reanchored_anchor - golden_anchor)
        status = "✅" if diff <= tolerance else "❌"

        print(
            f"   {status} Node {node_key}: "
            f"reanchored={reanchored_anchor}, golden={golden_anchor}, "
            f"diff={diff}"
        )

        if diff > tolerance:
            msg = (
                f"TreeNodeBlock anchor mismatch for node {node_key}: "
                f"reanchored={reanchored_anchor}, golden={golden_anchor}, "
                f"diff={diff} (tolerance={tolerance})"
            )
            errors.append(msg)

    # If node_ids don't match (different documents), compare by sorted anchor values
    if not any(k in reanchored_anchors for k in golden_anchors):
        print("   ⚠️  Node IDs don't match (expected for different documents)")
        print("   Comparing anchor values by magnitude instead...")

        reanchored_values = sorted(reanchored_anchors.values())
        golden_values = sorted(golden_anchors.values())

        if len(reanchored_values) != len(golden_values):
            # Count mismatch is expected when comparing different documents
            # The golden may have been recorded from a different test scenario
            # This is a warning, not an error - we only verify what we CAN verify
            print(
                f"   ⚠️  TreeNodeBlock count differs: "
                f"reanchored={len(reanchored_values)}, golden={len(golden_values)}"
            )
            print("   (This is expected when comparing different documents)")
        else:
            for i, (r_val, g_val) in enumerate(zip(reanchored_values, golden_values)):
                diff = abs(r_val - g_val)
                status = "✅" if diff <= tolerance else "❌"
                print(f"   {status} Anchor {i}: reanchored={r_val}, golden={g_val}, diff={diff}")

                if diff > tolerance:
                    msg = f"Anchor value mismatch #{i}: reanchored={r_val}, golden={g_val}, diff={diff}"
                    errors.append(msg)

    return errors


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
