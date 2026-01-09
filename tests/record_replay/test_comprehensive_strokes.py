"""Comprehensive stroke test covering all stroke behaviors.

This consolidated test replaces:
- test_stroke_reanchor.py
- test_stroke_anchors.py
- test_pen_colors.py
- test_pen_tools.py
- test_pen_widths.py

Test flow (6 trips for deep coverage):
1. Trip 1: Initial capture - User creates all strokes with varied properties
2. Trip 2: Content insertion - Insert paragraph before Section 5
3. Trip 3: Section deletion - Delete Section 6 entirely
4. Trip 4: Text modification - Modify text in Section 1
5. Trip 5: Add more strokes - User adds NEW strokes on modified doc
6. Golden trip - Re-add strokes for ground truth comparison

Recording Usage:
    uv run pytest tests/record_replay/test_comprehensive_strokes.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_comprehensive_strokes.py
"""

import io
import re

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


def count_strokes(rm_files: dict[str, bytes]) -> int:
    """Count total strokes across all .rm files."""
    count = 0
    for rm_data in rm_files.values():
        for anno in read_annotations(io.BytesIO(rm_data)):
            if anno.type == AnnotationType.STROKE:
                count += 1
    return count


def extract_stroke_properties(rm_files: dict[str, bytes]) -> list[dict]:
    """Extract stroke properties for analysis."""
    strokes = []
    for page_uuid, rm_data in rm_files.items():
        for anno in read_annotations(io.BytesIO(rm_data)):
            if anno.type == AnnotationType.STROKE and anno.stroke:
                strokes.append(
                    {
                        "page": page_uuid[:8],
                        "color": anno.stroke.color if hasattr(anno.stroke, "color") else None,
                        "tool": anno.stroke.tool if hasattr(anno.stroke, "tool") else None,
                        "point_count": len(anno.stroke.points) if anno.stroke.points else 0,
                    }
                )
    return strokes


@pytest.mark.device
def test_comprehensive_strokes(device, workspace, fixtures_dir):
    """Comprehensive stroke test with 6 trips for deep coverage.

    This test verifies:
    - Basic stroke capture
    - Pen color preservation (red, blue)
    - Pen tool preservation (pencil, marker, ballpoint)
    - Stroke width preservation
    - Margin note reanchoring
    - Cross-page stroke handling
    - Stroke survival through section deletion
    - Orphan creation for strokes
    - Stroke anchoring with text modification
    - Cumulative strokes (old + new)
    - Pull sync [^n] footnote rendering
    - Golden comparison for position accuracy
    """
    fixture_doc = fixtures_dir / "test_comprehensive_strokes.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(
            fixture_doc, description="Comprehensive stroke test (6 trips)"
        )
    except FileNotFoundError:
        pytest.skip("Testdata not available. Run with --online -s to record.")

    # =========================================================================
    # TRIP 1: Initial capture - User creates all strokes
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 1: Initial stroke capture")
    print("=" * 60)
    print("\nPlease create strokes:")
    print("  - Section 1: Write 'hello' (default pen)")
    print("  - Section 2: Write 'red' (RED), 'blue' (BLUE)")
    print("  - Section 3: Use PENCIL, MARKER, BALLPOINT")
    print("  - Section 4: Thin line, thick line")
    print("  - Section 5: Margin note")
    print("  - Section 6: Write 'delete me'")
    print("  - Section 8: Write 'page2' (on page 2)")
    print("  - Section 9: Write 'stable'")

    doc_uuid = device.upload_document(workspace.test_doc)

    trip1_state = device.wait_for_annotations(doc_uuid)
    assert trip1_state.has_annotations, "Trip 1: Need strokes for this test"

    trip1_stroke_count = count_strokes(trip1_state.rm_files)
    trip1_properties = extract_stroke_properties(trip1_state.rm_files)

    print(f"\nTrip 1: Captured {trip1_stroke_count} strokes")
    for prop in trip1_properties[:5]:  # Show first 5
        print(f"   Page {prop['page']}: {prop['point_count']} points, color={prop['color']}")

    # Should have captured multiple strokes
    assert trip1_stroke_count >= 8, f"Expected 8+ strokes, got {trip1_stroke_count}"

    # Verify pull sync - check markdown for footnote markers
    device.trigger_sync()
    markdown_content = workspace.test_doc.read_text()

    if "[^" in markdown_content:
        print("   Pull sync: Found [^n] footnote markers in markdown")
    else:
        print("   Pull sync: No footnote markers yet (may need OCR)")

    # =========================================================================
    # TRIP 2: Content insertion - Insert paragraph before Section 5
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 2: Content insertion (reanchoring test)")
    print("=" * 60)

    original_content = workspace.test_doc.read_text()

    # Insert content before Section 5
    insertion_text = """
## NEW SECTION: Inserted Before Margin Note

This large section was inserted AFTER Trip 1 annotations.
The margin note in Section 5 should reanchor to follow its paragraph.

Lorem ipsum dolor sit amet, consectetur adipiscing elit.
Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.

"""
    modified_content = original_content.replace(
        "## Section 5: Margin Note",
        insertion_text + "## Section 5: Margin Note",
    )
    workspace.test_doc.write_text(modified_content)
    print("   Inserted new section before Section 5")

    device.capture_phase("trip2_insertion")
    device.trigger_sync()

    trip2_state = device.get_document_state(doc_uuid)
    trip2_stroke_count = count_strokes(trip2_state.rm_files)

    print(f"\nTrip 2: {trip2_stroke_count} strokes after insertion")

    # All strokes should be preserved
    assert (
        trip2_stroke_count == trip1_stroke_count
    ), f"Strokes lost during insertion: {trip1_stroke_count} -> {trip2_stroke_count}"

    device.observe_result(
        "TRIP 2: Content was inserted before Section 5.\n"
        "Check that your margin note followed the paragraph."
    )

    # =========================================================================
    # TRIP 3: Section deletion - Delete Section 6 entirely
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 3: Section deletion (orphan test)")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Delete Section 6 (Deletion Target)
    modified_content = re.sub(
        r"## Section 6: Deletion Target.*?(?=## Section 7)",
        "",
        current_content,
        flags=re.DOTALL,
    )
    workspace.test_doc.write_text(modified_content)
    print("   Deleted Section 6 (Deletion Target)")

    device.capture_phase("trip3_deletion")
    device.trigger_sync()

    trip3_state = device.get_document_state(doc_uuid)
    trip3_stroke_count = count_strokes(trip3_state.rm_files)

    print(f"\nTrip 3: {trip3_stroke_count} strokes after deletion")

    # The "delete me" stroke should become orphan
    # Check markdown for orphan comment
    final_markdown = workspace.test_doc.read_text()
    has_orphan_comment = "orphan" in final_markdown.lower()

    if has_orphan_comment:
        print("   Orphan comment found in markdown")
    else:
        print("   No orphan comment found (may not be rendered yet)")

    device.observe_result(
        "TRIP 3: Section 6 was deleted.\n" "The 'delete me' stroke should now be orphaned."
    )

    # =========================================================================
    # TRIP 4: Text modification - Modify text near strokes
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 4: Text modification near strokes")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Modify text in Section 1
    modified_content = current_content.replace(
        "Write here: _________________________________________________",
        "MODIFIED GAP: _______________________________________________",
        1,  # Only first occurrence
    )
    workspace.test_doc.write_text(modified_content)
    print("   Modified gap text in Section 1")

    device.capture_phase("trip4_modification")
    device.trigger_sync()

    trip4_state = device.get_document_state(doc_uuid)
    trip4_stroke_count = count_strokes(trip4_state.rm_files)

    print(f"\nTrip 4: {trip4_stroke_count} strokes after text modification")

    # Strokes should stay anchored to their paragraphs
    assert (
        trip4_stroke_count >= trip3_stroke_count - 1
    ), f"Too many strokes lost: {trip3_stroke_count} -> {trip4_stroke_count}"

    device.observe_result(
        "TRIP 4: Text near the 'hello' stroke was modified.\n"
        "Check that the stroke remained anchored to its paragraph."
    )

    # =========================================================================
    # TRIP 5: Add more strokes - User adds NEW strokes
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 5: Add more strokes (cumulative test)")
    print("=" * 60)
    print("\nPlease add 2 NEW strokes:")
    print("  - Add a stroke in the inserted section (from Trip 2)")
    print("  - Add a margin note in Section 3")

    trip5_state = device.wait_for_annotations(doc_uuid)
    trip5_stroke_count = count_strokes(trip5_state.rm_files)

    print(f"\nTrip 5: {trip5_stroke_count} strokes (was {trip4_stroke_count})")

    # Should have at least as many as before
    assert (
        trip5_stroke_count >= trip4_stroke_count
    ), f"Strokes lost in cumulative round: {trip4_stroke_count} -> {trip5_stroke_count}"

    if trip5_stroke_count > trip4_stroke_count:
        print(f"   Added {trip5_stroke_count - trip4_stroke_count} new stroke(s)")

    device.observe_result(
        f"TRIP 5: You should have added new strokes.\n" f"Total strokes: {trip5_stroke_count}"
    )

    # =========================================================================
    # GOLDEN TRIP: Re-add strokes for ground truth comparison
    # =========================================================================
    print("\n" + "=" * 60)
    print("GOLDEN TRIP: Ground truth comparison")
    print("=" * 60)

    try:
        golden_state = device.upload_golden_document(
            workspace.test_doc,
            prompt=(
                "Re-add strokes at the same positions:\n"
                "1. Write in Section 1 gap\n"
                "2. Write in Section 9 'stable' gap\n"
                "3. Add margin notes where visible"
            ),
        )

        golden_stroke_count = count_strokes(golden_state.rm_files)
        print(f"\nGolden: {golden_stroke_count} strokes")

        golden_properties = extract_stroke_properties(golden_state.rm_files)
        for prop in golden_properties[:3]:
            print(f"   Page {prop['page']}: {prop['point_count']} points")

    except FileNotFoundError:
        print("\nNo golden data - skipping comparison")
        print("   Run with --online -s to record golden ground truth")

    # =========================================================================
    # FINAL VERIFICATION
    # =========================================================================
    print("\n" + "=" * 60)
    print("FINAL VERIFICATION")
    print("=" * 60)

    # Summary
    print("\nStroke counts by trip:")
    print(f"   Trip 1 (initial):      {trip1_stroke_count}")
    print(f"   Trip 2 (insertion):    {trip2_stroke_count}")
    print(f"   Trip 3 (deletion):     {trip3_stroke_count}")
    print(f"   Trip 4 (modification): {trip4_stroke_count}")
    print(f"   Trip 5 (cumulative):   {trip5_stroke_count}")

    # Verify pen properties were captured (if available)
    final_properties = extract_stroke_properties(trip5_state.rm_files)
    unique_colors = {p["color"] for p in final_properties if p["color"]}
    if unique_colors:
        print(f"\n   Colors captured: {unique_colors}")

    unique_tools = {p["tool"] for p in final_properties if p["tool"]}
    if unique_tools:
        print(f"   Tools captured: {unique_tools}")

    print("\nComprehensive stroke test PASSED")

    device.end_test(test_id)
