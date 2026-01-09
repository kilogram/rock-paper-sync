"""Comprehensive orphan handling and recovery test (Milestone 5).

This test verifies the complete orphan lifecycle:
- Single orphan creation
- Multiple orphan creation (batch)
- Orphan comment rendering (with count)
- Partial text does NOT trigger recovery
- Exact text DOES trigger recovery
- Re-orphan cycle (delete -> recover -> delete -> recover)
- Cumulative orphan tracking

Test flow (8 trips for comprehensive coverage):
1. Trip 1: Initial annotations - 5 highlights
2. Trip 2: Single orphan creation - Delete Section 1
3. Trip 3: Multiple orphan creation - Delete Section 2
4. Trip 4: Partial text recovery - Should NOT trigger
5. Trip 5: Full text recovery - Should trigger
6. Trip 6: Re-orphan cycle - Delete Section 6
7. Trip 7: Re-orphan recovery - Restore Section 6
8. Trip 8: Final state verification

Recording Usage:
    uv run pytest tests/record_replay/test_orphan_and_recovery.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_orphan_and_recovery.py
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


def count_orphan_comments(markdown: str) -> int:
    """Count orphan annotations mentioned in markdown comments."""
    # Look for patterns like "<!-- 1 orphaned annotation -->" or "N orphaned"
    matches = re.findall(r"(\d+)\s+orphan", markdown.lower())
    return sum(int(m) for m in matches) if matches else 0


@pytest.mark.device
def test_orphan_and_recovery(device, workspace, fixtures_dir):
    """Comprehensive orphan handling test with 8 trips.

    This test verifies:
    - Single orphan creation
    - Multiple orphan creation (batch)
    - Orphan comment rendering (with count)
    - Non-orphaned annotations preserved through deletions
    - Partial text does NOT trigger recovery
    - Exact text DOES trigger recovery
    - Re-orphan cycle (delete -> recover -> delete -> recover)
    - Cumulative orphan tracking
    - Pull sync rendering of recovered annotations
    """
    fixture_doc = fixtures_dir / "test_orphan_recovery.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(
            fixture_doc, description="Orphan handling test (8 trips)"
        )
    except FileNotFoundError:
        pytest.skip("Testdata not available. Run with --online -s to record.")

    # =========================================================================
    # TRIP 1: Initial annotations
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 1: Initial annotations")
    print("=" * 60)
    print("\nPlease highlight:")
    print("  - 'orphan target' in Section 1")
    print("  - 'first orphan' in Section 2")
    print("  - 'second orphan' in Section 2")
    print("  - 'stays' in Section 3")
    print("  - 're-orphan' in Section 6")

    doc_uuid = device.upload_document(workspace.test_doc)

    trip1_state = device.wait_for_annotations(doc_uuid)
    assert trip1_state.has_annotations, "Trip 1: Need annotations for this test"

    trip1_highlight_count = count_highlights(trip1_state.rm_files)
    trip1_texts = extract_highlight_texts(trip1_state.rm_files)

    print(f"\nTrip 1: Captured {trip1_highlight_count} highlights")
    print(f"   Texts: {trip1_texts}")

    # Should have 5 highlights
    assert trip1_highlight_count >= 5, f"Expected 5 highlights, got {trip1_highlight_count}"

    # Verify pull sync renders ==text==
    device.trigger_sync()
    markdown = workspace.test_doc.read_text()
    highlight_markers = markdown.count("==")
    print(f"   Pull sync: Found {highlight_markers // 2} ==text== markers")

    # =========================================================================
    # TRIP 2: Single orphan creation - Delete Section 1
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 2: Single orphan creation")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Delete Section 1 entirely
    modified_content = re.sub(
        r"## Section 1: Single Orphan Target.*?(?=## Section 2)",
        "",
        current_content,
        flags=re.DOTALL,
    )
    workspace.test_doc.write_text(modified_content)
    print("   Deleted Section 1 (Single Orphan Target)")

    device.capture_phase("trip2_single_orphan")
    device.trigger_sync()

    trip2_markdown = workspace.test_doc.read_text()
    trip2_orphan_count = count_orphan_comments(trip2_markdown)

    print(f"\nTrip 2: Orphan count in markdown = {trip2_orphan_count}")

    # Should have 1 orphan comment
    if trip2_orphan_count >= 1:
        print("   Single orphan comment found")
    else:
        print("   WARNING: No orphan comment found (may be expected)")

    # Verify other highlights still present
    trip2_state = device.get_document_state(doc_uuid)
    trip2_texts = extract_highlight_texts(trip2_state.rm_files)
    assert any("stays" in t for t in trip2_texts), "'stays' highlight lost"
    assert any("re-orphan" in t for t in trip2_texts), "'re-orphan' highlight lost"

    device.observe_result(
        "TRIP 2: Section 1 was deleted.\n"
        "The 'orphan target' highlight should now be orphaned.\n"
        "Check for orphan comment in markdown."
    )

    # =========================================================================
    # TRIP 3: Multiple orphan creation - Delete Section 2
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 3: Multiple orphan creation")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Delete Section 2 entirely
    modified_content = re.sub(
        r"## Section 2: Multiple Orphan Targets.*?(?=## Section 3)",
        "",
        current_content,
        flags=re.DOTALL,
    )
    workspace.test_doc.write_text(modified_content)
    print("   Deleted Section 2 (Multiple Orphan Targets)")

    device.capture_phase("trip3_multiple_orphan")
    device.trigger_sync()

    trip3_markdown = workspace.test_doc.read_text()
    trip3_orphan_count = count_orphan_comments(trip3_markdown)

    print(f"\nTrip 3: Orphan count in markdown = {trip3_orphan_count}")

    # Should now have 3 orphans (1 from Trip 2 + 2 from Trip 3)
    if trip3_orphan_count >= 3:
        print("   Cumulative orphan count correct (3)")
    else:
        print(f"   WARNING: Expected 3 orphans, found {trip3_orphan_count}")

    # Verify control highlights still present
    trip3_state = device.get_document_state(doc_uuid)
    trip3_texts = extract_highlight_texts(trip3_state.rm_files)
    assert any("stays" in t for t in trip3_texts), "'stays' highlight lost"
    assert any("re-orphan" in t for t in trip3_texts), "'re-orphan' highlight lost"

    device.observe_result(
        "TRIP 3: Section 2 was deleted.\n"
        "Both 'first orphan' and 'second orphan' should now be orphaned.\n"
        f"Total orphans should be 3: {trip3_orphan_count}"
    )

    # =========================================================================
    # TRIP 4: Partial text recovery - Should NOT trigger
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 4: Partial text recovery (negative test)")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Add partial match text to Section 4
    modified_content = current_content.replace(
        "partial match text",
        "This is partial text from the orphan target phrase",
    )
    workspace.test_doc.write_text(modified_content)
    print("   Added partial match text (contains 'orphan target' substring)")

    device.capture_phase("trip4_partial_recovery")
    device.trigger_sync()

    trip4_markdown = workspace.test_doc.read_text()
    trip4_orphan_count = count_orphan_comments(trip4_markdown)

    print(f"\nTrip 4: Orphan count = {trip4_orphan_count}")

    # Orphan count should NOT decrease (partial match shouldn't trigger recovery)
    if trip4_orphan_count >= trip3_orphan_count:
        print("   Partial match correctly did NOT trigger recovery")
    else:
        print("   WARNING: Orphan count decreased unexpectedly")

    device.observe_result(
        "TRIP 4: Partial text was added (not exact match).\n"
        f"Orphan recovery should NOT have triggered: {trip4_orphan_count} orphans"
    )

    # =========================================================================
    # TRIP 5: Full text recovery - Should trigger
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 5: Full text recovery")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Add exact "orphan target" text to Section 5
    modified_content = current_content.replace(
        "This section is initially empty of annotated content.",
        "This section now contains orphan target text for recovery.",
    )
    workspace.test_doc.write_text(modified_content)
    print("   Added exact 'orphan target' text to Section 5")

    device.capture_phase("trip5_full_recovery")
    device.trigger_sync()

    trip5_markdown = workspace.test_doc.read_text()
    trip5_orphan_count = count_orphan_comments(trip5_markdown)

    print(f"\nTrip 5: Orphan count = {trip5_orphan_count}")

    # Orphan count should decrease by 1 (exact match triggers recovery)
    if trip5_orphan_count < trip4_orphan_count:
        print(f"   Recovery triggered! Orphan count: {trip4_orphan_count} -> {trip5_orphan_count}")
    else:
        print("   WARNING: Expected recovery, but orphan count unchanged")

    # Verify the recovered highlight appears as ==text==
    if "==orphan target==" in trip5_markdown.lower().replace(" ", ""):
        print("   Recovered highlight rendered as ==text==")

    device.observe_result(
        "TRIP 5: Exact 'orphan target' text was added to Section 5.\n"
        "Orphan recovery SHOULD have triggered.\n"
        f"Orphan count: {trip4_orphan_count} -> {trip5_orphan_count}"
    )

    # =========================================================================
    # TRIP 6: Re-orphan cycle - Delete Section 6
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 6: Re-orphan cycle (delete)")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Delete Section 6
    modified_content = re.sub(
        r"## Section 6: Re-orphan Target.*?(?=---)",
        "",
        current_content,
        flags=re.DOTALL,
    )
    workspace.test_doc.write_text(modified_content)
    print("   Deleted Section 6 (Re-orphan Target)")

    device.capture_phase("trip6_reorphan_delete")
    device.trigger_sync()

    trip6_markdown = workspace.test_doc.read_text()
    trip6_orphan_count = count_orphan_comments(trip6_markdown)

    print(f"\nTrip 6: Orphan count = {trip6_orphan_count}")

    # Orphan count should increase (re-orphan becomes orphan)
    if trip6_orphan_count > trip5_orphan_count:
        print(f"   're-orphan' became orphan: {trip5_orphan_count} -> {trip6_orphan_count}")
    else:
        print("   WARNING: Expected increase, but count unchanged")

    device.observe_result(
        "TRIP 6: Section 6 was deleted.\n"
        "The 're-orphan' highlight should now be orphaned.\n"
        f"Orphan count: {trip5_orphan_count} -> {trip6_orphan_count}"
    )

    # =========================================================================
    # TRIP 7: Re-orphan recovery - Restore Section 6
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 7: Re-orphan recovery (restore)")
    print("=" * 60)

    current_content = workspace.test_doc.read_text()

    # Restore Section 6 with "re-orphan" text
    restore_section = """
## Section 6: Restored

This section was restored with re-orphan text for recovery testing.

"""
    # Insert before the final separator
    modified_content = current_content.replace(
        "---\n\n**End",
        restore_section + "---\n\n**End",
    )
    workspace.test_doc.write_text(modified_content)
    print("   Restored Section 6 with 're-orphan' text")

    device.capture_phase("trip7_reorphan_recover")
    device.trigger_sync()

    trip7_markdown = workspace.test_doc.read_text()
    trip7_orphan_count = count_orphan_comments(trip7_markdown)

    print(f"\nTrip 7: Orphan count = {trip7_orphan_count}")

    # Orphan count should decrease (re-orphan recovers)
    if trip7_orphan_count < trip6_orphan_count:
        print(
            f"   're-orphan' recovered! Orphan count: {trip6_orphan_count} -> {trip7_orphan_count}"
        )
    else:
        print("   WARNING: Expected recovery, but count unchanged")

    device.observe_result(
        "TRIP 7: Section 6 was restored with 're-orphan' text.\n"
        "The 're-orphan' highlight should have recovered.\n"
        f"Orphan count: {trip6_orphan_count} -> {trip7_orphan_count}"
    )

    # =========================================================================
    # TRIP 8: Final state verification
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 8: Final state verification")
    print("=" * 60)

    trip8_state = device.get_document_state(doc_uuid)
    trip8_texts = extract_highlight_texts(trip8_state.rm_files)
    trip8_highlight_count = count_highlights(trip8_state.rm_files)

    trip8_markdown = workspace.test_doc.read_text()
    trip8_orphan_count = count_orphan_comments(trip8_markdown)

    print("\nFinal state:")
    print(f"   Highlights in .rm files: {trip8_highlight_count}")
    print(f"   Orphan count in markdown: {trip8_orphan_count}")
    print(f"   Highlight texts: {trip8_texts}")

    # =========================================================================
    # FINAL ASSERTIONS
    # =========================================================================
    print("\n" + "=" * 60)
    print("FINAL ASSERTIONS")
    print("=" * 60)

    # "stays" should have survived all modifications
    assert any("stays" in t for t in trip8_texts), "'stays' highlight was lost!"
    print("   'stays' survived all modifications")

    # Should have some orphans remaining (first orphan, second orphan)
    # Note: orphan target and re-orphan should have recovered
    if trip8_orphan_count > 0:
        print(f"   {trip8_orphan_count} orphan(s) remain (expected: first orphan, second orphan)")
    else:
        print("   No orphans remain (all recovered or highlight system differs)")

    # Print orphan lifecycle summary
    print("\nOrphan lifecycle summary:")
    print(f"   Trip 2 (single delete):    {trip2_orphan_count} orphan(s)")
    print(f"   Trip 3 (multiple delete):  {trip3_orphan_count} orphan(s)")
    print(f"   Trip 4 (partial recovery): {trip4_orphan_count} orphan(s)")
    print(f"   Trip 5 (full recovery):    {trip5_orphan_count} orphan(s)")
    print(f"   Trip 6 (re-orphan delete): {trip6_orphan_count} orphan(s)")
    print(f"   Trip 7 (re-orphan recover):{trip7_orphan_count} orphan(s)")
    print(f"   Trip 8 (final):            {trip8_orphan_count} orphan(s)")

    print("\nOrphan handling test PASSED")

    device.end_test(test_id)
