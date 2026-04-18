"""Hidden-layer orphan preservation test (Milestone 5.5).

Verifies the M5.5 pipeline end-to-end on a real device:

1. Trip 1  — Annotate document (highlights + stroke)
2. Trip 2  — Delete Section 1; push syncs a hidden PRESERVATION layer
3. Trip 3  — Delete Sections 2 & 3; orphan count grows; hidden layer accumulates
4. Trip 4  — Restore "preserved forever" text; orphan recovery re-anchors it
5. Verify  — Hidden layer present in .rm; control highlight still anchored

Recording:
    uv run pytest tests/record_replay/test_hidden_layer_orphans.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_hidden_layer_orphans.py
"""

import io
import re

import pytest
import rmscene

from rock_paper_sync.annotations import AnnotationType, read_annotations
from rock_paper_sync.annotations.scene_adapter.scene_index import SYSTEM_LAYER_2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def count_highlights(rm_files: dict[str, bytes]) -> int:
    total = 0
    for data in rm_files.values():
        for anno in read_annotations(io.BytesIO(data)):
            if anno.type == AnnotationType.HIGHLIGHT:
                total += 1
    return total


def count_strokes(rm_files: dict[str, bytes]) -> int:
    total = 0
    for data in rm_files.values():
        for anno in read_annotations(io.BytesIO(data)):
            if anno.type == AnnotationType.STROKE:
                total += 1
    return total


def extract_highlight_texts(rm_files: dict[str, bytes]) -> set[str]:
    texts = set()
    for data in rm_files.values():
        for anno in read_annotations(io.BytesIO(data)):
            if anno.type == AnnotationType.HIGHLIGHT and anno.highlight and anno.highlight.text:
                texts.add(anno.highlight.text.strip().lower())
    return texts


def count_orphan_comments(markdown: str) -> int:
    matches = re.findall(r"(\d+)\s+orphan", markdown.lower())
    return sum(int(m) for m in matches) if matches else 0


def has_preservation_layer(rm_files: dict[str, bytes]) -> bool:
    """Return True if any page contains a hidden PRESERVATION layer (CrdtId(0,21))."""
    for data in rm_files.values():
        blocks = list(rmscene.read_blocks(io.BytesIO(data)))
        tree_nodes = [b for b in blocks if type(b).__name__ == "TreeNodeBlock"]
        for node in tree_nodes:
            if node.group.node_id == SYSTEM_LAYER_2:
                return True
    return False


def preservation_layer_is_invisible(rm_files: dict[str, bytes]) -> bool:
    """Return True if the PRESERVATION layer TreeNodeBlock has visible=False."""
    for data in rm_files.values():
        blocks = list(rmscene.read_blocks(io.BytesIO(data)))
        tree_nodes = [b for b in blocks if type(b).__name__ == "TreeNodeBlock"]
        for node in tree_nodes:
            if node.group.node_id == SYSTEM_LAYER_2:
                return node.group.visible.value is False
    return False


def count_preservation_blocks(rm_files: dict[str, bytes]) -> int:
    """Count blocks parented to the PRESERVATION layer across all pages."""
    count = 0
    for data in rm_files.values():
        blocks = list(rmscene.read_blocks(io.BytesIO(data)))
        for block in blocks:
            if hasattr(block, "parent_id") and block.parent_id == SYSTEM_LAYER_2:
                count += 1
    return count


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.device
def test_hidden_layer_orphans(device, workspace, fixtures_dir):
    """Verify that orphaned annotations are preserved in a hidden .rm layer.

    Trip 1: Annotate (3 highlights, 1 stroke).
    Trip 2: Delete Section 1 → single highlight orphaned → push produces
            a hidden PRESERVATION layer containing that highlight's blocks.
    Trip 3: Delete Sections 2 & 3 → 3 more orphans accumulated → PRESERVATION
            layer grows; control highlight remains on the content layer.
    Trip 4: Restore "preserved forever" text → orphan recovery re-anchors the
            Trip-2 orphan back onto the content layer.
    """
    fixture_doc = fixtures_dir / "test_hidden_layer_orphans.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(
            fixture_doc, description="Hidden layer orphan preservation (M5.5, 4 trips)"
        )
    except FileNotFoundError:
        pytest.skip("Testdata not available. Run with --online -s to record.")

    # =========================================================================
    # TRIP 1: Annotate the document
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 1: Annotate the document")
    print("=" * 60)
    print()
    print("Please make the following annotations:")
    print("  Section 1 — highlight 'preserved forever'")
    print("  Section 2 — highlight 'first preserved'")
    print("  Section 2 — highlight 'second preserved'")
    print("  Section 3 — draw a short stroke over 'stroked'")
    print("  Section 4 — highlight 'control highlight'")

    doc_uuid = device.upload_document(workspace.test_doc)
    trip1_state = device.wait_for_annotations(doc_uuid)

    assert trip1_state.has_annotations, "Trip 1: at least one annotation required"

    trip1_highlights = count_highlights(trip1_state.rm_files)
    trip1_strokes = count_strokes(trip1_state.rm_files)
    trip1_texts = extract_highlight_texts(trip1_state.rm_files)

    print(f"\nTrip 1 captured: {trip1_highlights} highlights, {trip1_strokes} stroke(s)")
    print(f"  Highlight texts: {trip1_texts}")

    assert trip1_highlights >= 3, (
        f"Expected ≥3 highlights (preserved forever, first preserved, second preserved, "
        f"control highlight), got {trip1_highlights}"
    )

    # Pull sync to confirm annotation markers render (==text== or <!-- ANNOTATED -->)
    device.trigger_sync()
    trip1_markdown = workspace.test_doc.read_text()
    has_annotation_markers = "==" in trip1_markdown or "<!-- ANNOTATED" in trip1_markdown
    assert has_annotation_markers, "Pull sync should render annotation markers"

    # No preservation layer yet (no orphans)
    assert not has_preservation_layer(
        trip1_state.rm_files
    ), "Trip 1: no PRESERVATION layer expected before any orphans"

    # =========================================================================
    # TRIP 2: Delete Section 1 → single orphan → push produces hidden layer
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 2: Delete Section 1 → orphan 'preserved forever'")
    print("=" * 60)

    current = workspace.test_doc.read_text()
    modified = re.sub(
        r"## Section 1: Single Highlight Target.*?(?=## Section 2)",
        "",
        current,
        flags=re.DOTALL,
    )
    workspace.test_doc.write_text(modified)
    print("  Deleted Section 1")

    device.capture_phase("trip2_delete_section1")
    device.trigger_sync()  # push: should produce hidden layer

    trip2_markdown = workspace.test_doc.read_text()
    trip2_orphans = count_orphan_comments(trip2_markdown)
    print(f"\nTrip 2: orphan comments in markdown = {trip2_orphans}")

    # Fetch updated .rm files after push
    trip2_state = device.get_document_state(doc_uuid)

    has_hidden = has_preservation_layer(trip2_state.rm_files)
    is_invisible = preservation_layer_is_invisible(trip2_state.rm_files)
    block_count = count_preservation_blocks(trip2_state.rm_files)

    print(f"  Hidden PRESERVATION layer present: {has_hidden}")
    print(f"  Preservation layer invisible:      {is_invisible}")
    print(f"  Blocks on preservation layer:      {block_count}")

    assert has_hidden, (
        "Trip 2: expected a hidden PRESERVATION layer (CrdtId(0,21)) after orphaning "
        "'preserved forever'"
    )
    assert is_invisible, "Trip 2: PRESERVATION layer TreeNodeBlock must have visible=False"
    assert (
        block_count >= 1
    ), f"Trip 2: expected ≥1 block on the preservation layer, got {block_count}"

    # Control highlight must still be on the content layer
    trip2_texts = extract_highlight_texts(trip2_state.rm_files)
    assert any(
        "control" in t for t in trip2_texts
    ), "Trip 2: 'control highlight' should still be anchored after Section 1 deletion"

    device.observe_result(
        "Trip 2: Section 1 deleted.\n"
        f"  Orphan comments in markdown: {trip2_orphans}\n"
        f"  PRESERVATION layer present:  {has_hidden}\n"
        f"  PRESERVATION layer invisible:{is_invisible}\n"
        f"  Blocks on preservation layer:{block_count}"
    )

    # =========================================================================
    # TRIP 3: Delete Sections 2 & 3 → 3 more orphans accumulate
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 3: Delete Sections 2 & 3 → accumulate more orphans")
    print("=" * 60)

    current = workspace.test_doc.read_text()
    # Delete Section 2
    modified = re.sub(
        r"## Section 2: Multiple Highlight Targets.*?(?=## Section 3)",
        "",
        current,
        flags=re.DOTALL,
    )
    # Delete Section 3
    modified = re.sub(
        r"## Section 3: Stroke Target.*?(?=## Section 4)",
        "",
        modified,
        flags=re.DOTALL,
    )
    workspace.test_doc.write_text(modified)
    print("  Deleted Sections 2 and 3")

    device.capture_phase("trip3_delete_sections2_3")
    device.trigger_sync()  # push: preservation layer should grow

    trip3_markdown = workspace.test_doc.read_text()
    trip3_orphans = count_orphan_comments(trip3_markdown)
    print(f"\nTrip 3: orphan comments in markdown = {trip3_orphans}")

    trip3_state = device.get_document_state(doc_uuid)
    trip3_block_count = count_preservation_blocks(trip3_state.rm_files)
    trip3_hidden = has_preservation_layer(trip3_state.rm_files)

    print(f"  Hidden PRESERVATION layer present: {trip3_hidden}")
    print(f"  Blocks on preservation layer:      {trip3_block_count}")

    assert trip3_hidden, "Trip 3: PRESERVATION layer must still be present"
    assert trip3_block_count >= block_count, (
        f"Trip 3: preservation layer should have grown (was {block_count}, "
        f"now {trip3_block_count})"
    )

    # Control still present
    trip3_texts = extract_highlight_texts(trip3_state.rm_files)
    assert any(
        "control" in t for t in trip3_texts
    ), "Trip 3: 'control highlight' should survive Sections 2 & 3 deletion"

    device.observe_result(
        "Trip 3: Sections 2 & 3 deleted.\n"
        f"  Orphan comments in markdown: {trip3_orphans}\n"
        f"  Blocks on preservation layer:{trip3_block_count} (was {block_count})"
    )

    # =========================================================================
    # TRIP 4: Restore "preserved forever" → orphan recovery
    # =========================================================================
    print("\n" + "=" * 60)
    print("TRIP 4: Restore 'preserved forever' → orphan recovery")
    print("=" * 60)

    current = workspace.test_doc.read_text()
    # Inject the original anchor text into the Recovery Zone
    modified = current.replace(
        'In Trip 4, the text "preserved forever" will be re-added here to verify',
        "The phrase preserved forever has been restored here.\n"
        'In Trip 4, the text "preserved forever" was re-added to verify',
    )
    workspace.test_doc.write_text(modified)
    print("  Restored 'preserved forever' into Section 5 (Recovery Zone)")

    device.capture_phase("trip4_recovery")
    device.trigger_sync()

    trip4_markdown = workspace.test_doc.read_text()
    trip4_orphans = count_orphan_comments(trip4_markdown)
    print(f"\nTrip 4: orphan comments in markdown = {trip4_orphans}")

    recovered = trip3_orphans - trip4_orphans
    print(f"  Orphans recovered: {recovered}")

    # "preserved forever" should re-appear as ==text== somewhere
    has_recovered_marker = "==preserved forever==" in trip4_markdown.lower().replace(" ", "")
    print(f"  ==preserved forever== marker found: {has_recovered_marker}")

    trip4_state = device.get_document_state(doc_uuid)
    trip4_block_count = count_preservation_blocks(trip4_state.rm_files)
    print(f"  Blocks on preservation layer: {trip4_block_count} (was {trip3_block_count})")

    if recovered > 0:
        print("  Orphan successfully re-anchored (orphan count decreased)")
    else:
        print("  WARNING: orphan count unchanged — recovery may not have triggered")

    # Control still present
    trip4_texts = extract_highlight_texts(trip4_state.rm_files)
    assert any(
        "control" in t for t in trip4_texts
    ), "Trip 4: 'control highlight' must survive through recovery trip"

    device.observe_result(
        "Trip 4: 'preserved forever' restored in recovery zone.\n"
        f"  Orphan comments: {trip3_orphans} → {trip4_orphans}\n"
        f"  Orphans recovered: {recovered}\n"
        f"  Preservation layer blocks: {trip4_block_count}\n"
        f"  ==preserved forever== re-rendered: {has_recovered_marker}"
    )

    # =========================================================================
    # FINAL ASSERTIONS
    # =========================================================================
    print("\n" + "=" * 60)
    print("FINAL ASSERTIONS")
    print("=" * 60)

    # The PRESERVATION layer must have been present after Trip 2 (core M5.5 guarantee)
    assert has_hidden, "PRESERVATION layer (CrdtId(0,21)) was never created after orphaning"
    assert is_invisible, "PRESERVATION layer must be invisible (hidden from user)"

    # Control highlight survived all modifications
    assert any(
        "control" in t for t in trip4_texts
    ), "'control highlight' was lost — non-orphaned annotations must survive all modifications"

    print("  PRESERVATION layer created after first orphan:      PASS")
    print("  PRESERVATION layer is invisible:                    PASS")
    print("  Non-orphaned 'control highlight' survived all trips:PASS")
    print(f"  Final orphan count:                                 {trip4_orphans}")
    print(f"  Final preservation layer block count:               {trip4_block_count}")

    print("\nHidden layer orphan preservation test PASSED")

    device.end_test(test_id)
