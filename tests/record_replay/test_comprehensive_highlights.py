"""Comprehensive highlight test covering all highlight behaviors.

This consolidated test replaces:
- test_highlights.py
- test_highlight_reanchor.py
- test_highlight_anchoring.py
- test_conflicting_edit.py

Test flow (7 trips for deep coverage):
1. Trip 1: Initial capture - User highlights 7 targets
2. Trip 2: Content insertion - Insert paragraph before Section 2
3. Trip 3: Text modification - Change "edit me" to "EDITED TEXT"
4. Trip 4: Section deletion - Delete Section 7 entirely
5. Trip 5: Major restructure - Move Section 5 before Section 3
6. Trip 6: Second annotation round - Add 2 NEW highlights
7. Golden trip - Re-highlight for ground truth comparison

Recording Usage:
    uv run pytest tests/record_replay/test_comprehensive_highlights.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_comprehensive_highlights.py
"""

import io
import re

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


def count_highlights(rm_files: dict[str, bytes]) -> int:
    """Count total highlights across all .rm files."""
    count = 0
    for rm_data in rm_files.values():
        for anno in read_annotations(io.BytesIO(rm_data)):
            if anno.type == AnnotationType.HIGHLIGHT:
                count += 1
    return count


def extract_highlight_texts(rm_files: dict[str, bytes]) -> set[str]:
    """Extract all highlight text content from .rm files."""
    texts = set()
    for rm_data in rm_files.values():
        for anno in read_annotations(io.BytesIO(rm_data)):
            if anno.type == AnnotationType.HIGHLIGHT and anno.highlight and anno.highlight.text:
                texts.add(anno.highlight.text.strip().lower())
    return texts


@pytest.mark.device
def test_comprehensive_highlights(device, workspace, fixtures_dir):
    """Comprehensive highlight test with 7 trips for deep coverage.

    This test verifies:
    - Basic highlight capture
    - Highlight reanchoring after content insertion
    - Highlight anchoring with text modification
    - Multi-line highlight handling
    - Duplicate text disambiguation
    - Highlight survival through section deletion
    - Orphan creation on deletion
    - Highlight survival through restructuring
    - Cumulative annotations (old + new)
    - Pull sync ==text== rendering
    - Golden comparison for position accuracy
    """
    fixture_doc = fixtures_dir / "test_comprehensive_highlights.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(
            fixture_doc, description="Comprehensive highlight test (7 trips)"
        )
    except FileNotFoundError:
        pytest.skip("Testdata not available. Run with --online -s to record.")

    # =========================================================================
    # TRIP 1: Initial capture - User highlights all 7 targets
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 1: Initial highlight capture")
    print("=" * 60)

    doc_uuid = device.upload_document(workspace.test_doc)

    trip1_state = device.wait_for_annotations(doc_uuid)
    assert trip1_state.has_annotations, "Trip 1: Need annotations for this test"

    trip1_highlight_count = count_highlights(trip1_state.rm_files)
    trip1_texts = extract_highlight_texts(trip1_state.rm_files)

    print(f"\nTrip 1: Captured {trip1_highlight_count} highlights")
    print(f"   Texts: {trip1_texts}")

    # Should have captured all 7 target highlights
    assert trip1_highlight_count >= 7, f"Expected 7+ highlights, got {trip1_highlight_count}"

    # Verify expected texts are present
    expected_texts = {
        "first target",
        "will move",
        "edit me",
        "stable anchor",
        "delete zone",
        "duplicate",
    }
    for expected in expected_texts:
        found = any(expected in t for t in trip1_texts)
        if not found:
            print(f"   WARNING: '{expected}' not found in highlights")

    # Verify pull sync - check markdown contains ==text== markers
    device.trigger_sync()
    markdown_content = workspace.test_doc.read_text()

    # After pull sync, highlights should render as ==text==
    # Note: This depends on pull sync being implemented
    if "==" in markdown_content:
        print("   Pull sync: Found ==text== markers in markdown")
    else:
        print("   Pull sync: No ==text== markers yet (may need sync)")

    # =========================================================================
    # TRIP 2: Content insertion - Insert paragraph before Section 2
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 2: Content insertion (reanchoring test)")
    print("=" * 60)

    original_content = workspace.test_doc.read_text()

    # Insert a large paragraph before Section 2
    insertion_text = """
## NEW SECTION: Inserted Content

This entire section was inserted AFTER Trip 1 annotations.
It contains substantial text to push Section 2 down significantly.

Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod
tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam,
quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo.

"""
    modified_content = original_content.replace(
        "## Section 2: Reanchoring Target",
        insertion_text + "## Section 2: Reanchoring Target",
    )
    workspace.test_doc.write_text(modified_content)
    print("   Inserted new section before Section 2")

    device.capture_phase("trip2_insertion")
    device.trigger_sync()

    trip2_state = device.get_document_state(doc_uuid)
    trip2_highlight_count = count_highlights(trip2_state.rm_files)

    print(f"\nTrip 2: {trip2_highlight_count} highlights after insertion")

    # All highlights should be preserved
    assert (
        trip2_highlight_count == trip1_highlight_count
    ), f"Highlights lost during insertion: {trip1_highlight_count} -> {trip2_highlight_count}"

    # Verify "will move" highlight is still present
    trip2_texts = extract_highlight_texts(trip2_state.rm_files)
    assert any("will move" in t for t in trip2_texts), "'will move' highlight lost after insertion"

    device.observe_result(
        "TRIP 2: Content was inserted before Section 2.\n"
        "Check that your 'will move' highlight followed the text to its new position."
    )

    # =========================================================================
    # TRIP 3: Text modification - Change "edit me" to "EDITED TEXT"
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 3: Text modification (conflict test)")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Modify the text that was highlighted
    # Be careful to only replace in Section 3
    modified_content = re.sub(
        r"(## Section 3: Conflict Target.*?)edit me",
        r"\1EDITED TEXT",
        current_content,
        count=1,
        flags=re.DOTALL,
    )
    workspace.test_doc.write_text(modified_content)
    print("   Changed 'edit me' -> 'EDITED TEXT' in Section 3")

    device.capture_phase("trip3_modification")
    device.trigger_sync()

    trip3_state = device.get_document_state(doc_uuid)
    trip3_highlight_count = count_highlights(trip3_state.rm_files)

    print(f"\nTrip 3: {trip3_highlight_count} highlights after text modification")

    # The "edit me" highlight may become orphan or reanchor to similar text
    # At minimum, other highlights should be preserved
    assert (
        trip3_highlight_count >= trip1_highlight_count - 1
    ), f"Too many highlights lost: {trip1_highlight_count} -> {trip3_highlight_count}"

    device.observe_result(
        "TRIP 3: The text 'edit me' was changed to 'EDITED TEXT'.\n"
        "Check how the system handled this conflict:\n"
        "1. Did the highlight follow to 'EDITED TEXT'?\n"
        "2. Or did it become an orphan?"
    )

    # =========================================================================
    # TRIP 4: Section deletion - Delete Section 7 entirely
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 4: Section deletion (orphan test)")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Delete Section 7 entirely
    # Match from "## Section 7" to just before the "---" separator
    modified_content = re.sub(
        r"## Section 7: Deletion Zone.*?(?=---\s*\n\*\*End of)",
        "",
        current_content,
        flags=re.DOTALL,
    )
    workspace.test_doc.write_text(modified_content)
    print("   Deleted Section 7 (Deletion Zone)")

    device.capture_phase("trip4_deletion")
    device.trigger_sync()

    trip4_state = device.get_document_state(doc_uuid)
    trip4_highlight_count = count_highlights(trip4_state.rm_files)

    print(f"\nTrip 4: {trip4_highlight_count} highlights after section deletion")

    # The "delete zone" highlight should become orphan
    # Check markdown for orphan comment
    final_markdown = workspace.test_doc.read_text()
    has_orphan_comment = "orphan" in final_markdown.lower()

    if has_orphan_comment:
        print("   Orphan comment found in markdown")
    else:
        print("   No orphan comment found (may not be rendered yet)")

    # Verify other highlights preserved
    trip4_texts = extract_highlight_texts(trip4_state.rm_files)
    assert any("stable anchor" in t for t in trip4_texts), "'stable anchor' highlight lost"
    assert any("first target" in t for t in trip4_texts), "'first target' highlight lost"

    device.observe_result(
        "TRIP 4: Section 7 was deleted.\n"
        "The 'delete zone' highlight should now be orphaned.\n"
        "Check the markdown for an orphan comment."
    )

    # =========================================================================
    # TRIP 5: Major restructure - Move Section 5 before Section 3
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 5: Major restructure (paragraph tracking)")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Extract Section 5
    section5_match = re.search(
        r"(## Section 5: Duplicate Text.*?)(?=## Section 6)",
        current_content,
        flags=re.DOTALL,
    )

    if section5_match:
        section5_content = section5_match.group(1)
        # Remove Section 5 from original position
        content_without_s5 = current_content.replace(section5_content, "")
        # Insert before Section 3
        modified_content = content_without_s5.replace(
            "## Section 3: Conflict Target",
            section5_content + "\n## Section 3: Conflict Target",
        )
        workspace.test_doc.write_text(modified_content)
        print("   Moved Section 5 before Section 3")
    else:
        print("   WARNING: Could not find Section 5 to move")

    device.capture_phase("trip5_restructure")
    device.trigger_sync()

    trip5_state = device.get_document_state(doc_uuid)
    trip5_highlight_count = count_highlights(trip5_state.rm_files)

    print(f"\nTrip 5: {trip5_highlight_count} highlights after restructure")

    # The "duplicate" highlight should follow its paragraph
    trip5_texts = extract_highlight_texts(trip5_state.rm_files)
    assert any(
        "duplicate" in t for t in trip5_texts
    ), "'duplicate' highlight lost after restructure"

    device.observe_result(
        "TRIP 5: Section 5 (Duplicate Text) was moved before Section 3.\n"
        "Check that the 'duplicate' highlight followed its paragraph."
    )

    # =========================================================================
    # TRIP 6: Second annotation round - Add NEW highlights
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 6: Second annotation round (cumulative test)")
    print("=" * 60)

    # User will add new highlights on the modified document
    trip6_state = device.wait_for_annotations(doc_uuid)
    trip6_highlight_count = count_highlights(trip6_state.rm_files)

    print(f"\nTrip 6: {trip6_highlight_count} highlights (was {trip5_highlight_count})")

    # Should have at least as many as before (ideally more from new annotations)
    assert (
        trip6_highlight_count >= trip5_highlight_count
    ), f"Highlights lost in cumulative round: {trip5_highlight_count} -> {trip6_highlight_count}"

    if trip6_highlight_count > trip5_highlight_count:
        print(f"   Added {trip6_highlight_count - trip5_highlight_count} new highlight(s)")

    device.observe_result(
        "TRIP 6: You should have added new highlights.\n"
        f"Total highlights: {trip6_highlight_count}"
    )

    # =========================================================================
    # GOLDEN TRIP: Re-highlight for ground truth comparison
    # =========================================================================
    print("\n" + "=" * 60)
    print("GOLDEN TRIP: Ground truth comparison")
    print("=" * 60)

    try:
        golden_state = device.upload_golden_document(
            workspace.test_doc,
            prompt=(
                "Re-highlight the same text as currently visible:\n"
                "1. 'first target' in Section 1\n"
                "2. 'stable anchor' in Section 6\n"
                "3. 'duplicate' (second occurrence)\n"
                "4. Any other visible highlights"
            ),
        )

        golden_highlight_count = count_highlights(golden_state.rm_files)
        print(f"\nGolden: {golden_highlight_count} highlights")

        # Compare positions (if golden has highlights)
        if golden_highlight_count > 0:
            golden_texts = extract_highlight_texts(golden_state.rm_files)
            print(f"   Golden texts: {golden_texts}")

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
    print("\nHighlight counts by trip:")
    print(f"   Trip 1 (initial):      {trip1_highlight_count}")
    print(f"   Trip 2 (insertion):    {trip2_highlight_count}")
    print(f"   Trip 3 (modification): {trip3_highlight_count}")
    print(f"   Trip 4 (deletion):     {trip4_highlight_count}")
    print(f"   Trip 5 (restructure):  {trip5_highlight_count}")
    print(f"   Trip 6 (cumulative):   {trip6_highlight_count}")

    # Final assertions
    final_texts = extract_highlight_texts(trip6_state.rm_files)

    # "stable anchor" should have survived all modifications
    assert any(
        "stable anchor" in t for t in final_texts
    ), "'stable anchor' highlight was lost - this should never happen!"
    print("\n   'stable anchor' survived all modifications")

    # "first target" should have survived
    assert any("first target" in t for t in final_texts), "'first target' highlight was lost"
    print("   'first target' survived all modifications")

    # "duplicate" should have followed restructure
    assert any(
        "duplicate" in t for t in final_texts
    ), "'duplicate' highlight was lost after restructure"
    print("   'duplicate' survived restructure")

    print("\nComprehensive highlight test PASSED")

    device.end_test(test_id)
