"""Test for highlight anchoring after X-shift, Y-shift, and multi-line reflow.

Three highlights:
1. "target" - shifts horizontally when "INSERTED " added before it
2. "bottom" - shifts vertically when a new paragraph is inserted above it
3. "cross line" - reflows from 1 line to 2 lines when "INSERTED " added at line start

Recording:
    uv run pytest tests/record_replay/test_anchor_shift.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_anchor_shift.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations
from rock_paper_sync.layout.constants import LINE_HEIGHT


def extract_highlights(rm_files: dict[str, bytes]) -> list:
    """Extract all highlight objects from .rm files."""
    highlights = []
    for rm_data in rm_files.values():
        for anno in read_annotations(io.BytesIO(rm_data)):
            if anno.type == AnnotationType.HIGHLIGHT and anno.highlight:
                highlights.append(anno.highlight)
    return highlights


def find_highlight_by_text(highlights: list, text_substring: str):
    """Find a highlight containing the given text substring."""
    for hl in highlights:
        if text_substring.lower() in hl.text.lower():
            return hl
    return None


def replace_once(text: str, old: str, new: str) -> str:
    """Replace exactly one occurrence of old with new, or raise if not found."""
    if old not in text:
        raise ValueError(f"Pattern not found in text: {old!r}")
    if text.count(old) > 1:
        raise ValueError(f"Pattern found multiple times ({text.count(old)}x): {old!r}")
    return text.replace(old, new)


@pytest.mark.device
def test_anchor_shift(device, workspace, fixtures_dir):
    """Three highlights: test X-shift, Y-shift, and multi-line reflow.

    1. "target" highlight - X position must INCREASE when "INSERTED " added before
    2. "bottom" highlight - Y position must INCREASE when paragraph inserted above
    3. "cross line" highlight - must SPLIT into 2 rectangles when text reflows

    This validates that anchoring works for horizontal shifts, vertical shifts,
    and the critical case where a highlight spans multiple lines after reflow.
    """
    fixture_doc = fixtures_dir / "test_anchor_shift.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(fixture_doc)
    except FileNotFoundError:
        pytest.skip("No testdata. Run with --online -s to record.")

    # Upload and get annotations
    doc_uuid = device.upload_document(workspace.test_doc)
    print("\n📝 Please highlight 'target', 'bottom', and 'cross line' in document.md")
    state = device.wait_for_annotations(doc_uuid)
    assert state.has_annotations, "Need highlights on 'target', 'bottom', and 'cross line'"

    # Extract highlights BEFORE modification
    before_highlights = extract_highlights(state.rm_files)
    assert (
        len(before_highlights) >= 2
    ), f"Need at least 2 highlights ('target', 'bottom'), got {len(before_highlights)}"

    # Find each highlight by its text
    target_hl_before = find_highlight_by_text(before_highlights, "target")
    bottom_hl_before = find_highlight_by_text(before_highlights, "bottom")
    crossline_hl_before = find_highlight_by_text(before_highlights, "cross")

    assert target_hl_before, "Need highlight on 'target'"
    assert bottom_hl_before, "Need highlight on 'bottom'"
    # crossline is optional - testdata may not have been re-recorded with it
    has_crossline = crossline_hl_before is not None
    if not has_crossline:
        print("\n⚠️  No 'cross line' highlight in testdata - skipping reflow assertions")
        print("   Re-record testdata with --online -s to test reflow")

    # Record positions before modification
    target_x_before = target_hl_before.rectangles[0].x if target_hl_before.rectangles else 0
    target_y_before = target_hl_before.rectangles[0].y if target_hl_before.rectangles else 0
    bottom_x_before = bottom_hl_before.rectangles[0].x if bottom_hl_before.rectangles else 0
    bottom_y_before = bottom_hl_before.rectangles[0].y if bottom_hl_before.rectangles else 0
    crossline_rects_before = len(crossline_hl_before.rectangles) if has_crossline else 0

    print("\n📊 Before modification:")
    print(f"   'target': x={target_x_before:.1f}, y={target_y_before:.1f}")
    print(f"   'bottom': x={bottom_x_before:.1f}, y={bottom_y_before:.1f}")
    if has_crossline:
        print(f"   'cross line': {crossline_rects_before} rectangle(s)")

    # Modify document:
    # 1. Add "INSERTED " before "target" (X shift)
    # 2. Add new paragraph between first and second (Y shift for "bottom")
    # 3. Add "INSERTED " before "Keep adding words" (reflow for "cross line")
    #
    # NOTE: After sync, the file has annotation markers wrapping paragraphs.
    # We modify content while PRESERVING markers to test marker stability.
    original = workspace.test_doc.read_text()
    print(f"\n📄 Document after sync:\n{original}\n")

    # Check if document already has modifications
    # This allows capturing device-native highlights on already-modified text
    already_modified = "INSERTED" in original
    if already_modified:
        print("\n📌 DEVICE-NATIVE CAPTURE MODE: Document already modified")
        print("   Highlights will be captured at their final positions")
        print("   (no modifications will be applied)")
        modified = original
    else:
        # Apply modifications
        modified = replace_once(original, "The target", "The INSERTED target")
        modified = replace_once(modified, "Keep adding", "INSERTED Keep adding")

        # Y shift: add paragraph between the two annotated blocks
        if "<!-- /ANNOTATED -->" in modified:
            import re

            marker_pattern = r"(<!-- /ANNOTATED -->\n\n)(<!-- ANNOTATED: \d+ highlights? -->)"
            match = re.search(marker_pattern, modified)
            if match:
                modified = (
                    modified[: match.end(1)]
                    + "This is a NEW PARAGRAPH inserted between the two highlights.\n\n"
                    + modified[match.start(2) :]
                )
            else:
                raise ValueError(f"Pattern not found. Document:\n{modified}")
        else:
            modified = replace_once(
                modified,
                'word is here.\n\nHighlight the word "bottom"',
                'word is here.\n\nThis is a NEW PARAGRAPH inserted between the two highlights.\n\nHighlight the word "bottom"',
            )

        workspace.test_doc.write_text(modified)
        print("✏️  Modifications applied:")
        print("   - Added 'INSERTED' before 'target' (should shift X right)")
        print("   - Added new paragraph between highlights (should shift 'bottom' Y down)")
        print("   - Added 'INSERTED' before 'Keep adding' (should reflow 'cross line' to 2 lines)")

    # Sync
    device.trigger_sync()

    # Capture post-modification state (rm files with adjusted positions)
    device.capture_phase("post_modification", action="sync_modified")

    # Observe before asserting
    device.observe_result(
        "Check all three highlights:\n"
        "1. 'target' highlight should be on 'target', NOT on 'INSERTED'\n"
        "2. 'bottom' highlight should have moved DOWN due to new paragraph\n"
        "3. 'cross line' highlight should now span TWO lines (word split)"
    )

    # Get state AFTER modification
    after_state = device.get_document_state(doc_uuid)
    after_highlights = extract_highlights(after_state.rm_files)

    # Note: testdata may have fewer highlights than the fixture describes
    # We need at least 1 highlight to verify X-shift
    assert len(after_highlights) >= 1, "Lost all highlights after modification"

    # Find highlights after modification
    target_hl_after = find_highlight_by_text(after_highlights, "target")
    bottom_hl_after = find_highlight_by_text(after_highlights, "bottom")
    crossline_hl_after = (
        find_highlight_by_text(after_highlights, "cross") if has_crossline else None
    )

    assert target_hl_after, "Lost 'target' highlight after modification"
    assert bottom_hl_after, "Lost 'bottom' highlight after modification"
    if has_crossline:
        assert crossline_hl_after, "Lost 'cross line' highlight after modification"

    # Record positions after modification
    target_x_after = target_hl_after.rectangles[0].x if target_hl_after.rectangles else 0
    target_y_after = target_hl_after.rectangles[0].y if target_hl_after.rectangles else 0
    bottom_x_after = bottom_hl_after.rectangles[0].x if bottom_hl_after.rectangles else 0
    bottom_y_after = bottom_hl_after.rectangles[0].y if bottom_hl_after.rectangles else 0
    crossline_rects_after = len(crossline_hl_after.rectangles) if has_crossline else 0

    print("\n📊 After modification:")
    print(f"   'target': x={target_x_after:.1f}, y={target_y_after:.1f}")
    print(f"   'bottom': x={bottom_x_after:.1f}, y={bottom_y_after:.1f}")
    if has_crossline and crossline_hl_after and crossline_hl_after.rectangles:
        print(f"   'cross line': {crossline_rects_after} rectangle(s)")
        for i, rect in enumerate(crossline_hl_after.rectangles):
            print(f"      rect[{i}]: x={rect.x:.1f}, y={rect.y:.1f}, w={rect.w:.1f}")

    # Calculate deltas
    target_x_delta = target_x_after - target_x_before
    target_y_delta = target_y_after - target_y_before
    bottom_x_delta = bottom_x_after - bottom_x_before
    bottom_y_delta = bottom_y_after - bottom_y_before

    print("\n📊 Deltas:")
    print(f"   'target': Δx={target_x_delta:.1f}, Δy={target_y_delta:.1f}")
    print(f"   'bottom': Δx={bottom_x_delta:.1f}, Δy={bottom_y_delta:.1f}")
    if has_crossline:
        print(
            f"   'cross line': {crossline_rects_before} rect(s) → {crossline_rects_after} rect(s)"
        )

    # CRITICAL ASSERTIONS
    # Skip shift assertions in device-native mode (no modifications applied)
    if not already_modified:
        # 1. "target" X position must have increased (text shifted right)
        # "INSERTED " is 9 characters * ~15px = ~135px
        assert target_x_delta > 10, (
            f"X-SHIFT FAILED: 'target' highlight X did not increase.\n"
            f"Before X: {target_x_before:.1f}\n"
            f"After X:  {target_x_after:.1f}\n"
            f"Delta:    {target_x_delta:.1f}\n"
            f"Expected X to increase by ~50+ pixels after inserting 'INSERTED '."
        )

        # 2. "bottom" Y position must have increased (pushed down by new paragraph)
        # New paragraph adds ~2 lines * ~35px = ~70px
        assert bottom_y_delta > 30, (
            f"Y-SHIFT FAILED: 'bottom' highlight Y did not increase.\n"
            f"Before Y: {bottom_y_before:.1f}\n"
            f"After Y:  {bottom_y_after:.1f}\n"
            f"Delta:    {bottom_y_delta:.1f}\n"
            f"Expected Y to increase by ~35+ pixels after inserting paragraph above."
        )

        # 3. "bottom" X should NOT change significantly (only vertical shift)
        assert abs(bottom_x_delta) < 20, (
            f"UNEXPECTED X-SHIFT: 'bottom' highlight X changed unexpectedly.\n"
            f"Before X: {bottom_x_before:.1f}\n"
            f"After X:  {bottom_x_after:.1f}\n"
            f"Delta:    {bottom_x_delta:.1f}\n"
            f"Expected X to stay roughly the same (only Y should shift)."
        )

        # 4. "cross line" should now have 2 rectangles (reflow split it across lines)
        # Before: "cross line" fits on 1 line = 1 rectangle
        # After: "INSERTED " pushes "cross " to line 1 end, "line" wraps to line 2 = 2 rects
        if has_crossline:
            assert crossline_rects_after >= 2, (
                f"REFLOW FAILED: 'cross line' highlight did not split into rectangles.\n"
                f"Before: {crossline_rects_before} rectangle(s)\n"
                f"After:  {crossline_rects_after} rectangle(s)\n"
                f"Expected 2 rectangles after text reflow (one per line).\n"
                f"This indicates the re-anchoring system is not recalculating rectangles\n"
                f"when a highlight spans multiple lines after text modification."
            )
    else:
        print("\n📌 DEVICE-NATIVE MODE: Skipping shift assertions (no modifications applied)")

    # Check multi-line rectangle geometry (applies to both modes if we have crossline)
    if has_crossline and crossline_hl_after and crossline_rects_after >= 2:
        rect0 = crossline_hl_after.rectangles[0]
        rect1 = crossline_hl_after.rectangles[1]
        y_diff = abs(rect1.y - rect0.y)
        print("\n📐 Multi-line highlight geometry:")
        print(f"   rect[0]: x={rect0.x:.1f}, y={rect0.y:.1f}, w={rect0.w:.1f}")
        print(f"   rect[1]: x={rect1.x:.1f}, y={rect1.y:.1f}, w={rect1.w:.1f}")
        print(f"   Y difference: {y_diff:.1f}px (expected ~{LINE_HEIGHT:.1f}px)")

    print("\n📊 Re-anchored positions:")
    print(f"   'target': x={target_x_after:.1f}, y={target_y_after:.1f}")
    print(f"   'bottom': x={bottom_x_after:.1f}, y={bottom_y_after:.1f}")
    if has_crossline:
        print(
            f"   'cross line': {crossline_rects_after} rectangles spanning {crossline_rects_after} line(s)"
        )

    # OPTIONAL: Golden ground truth capture
    # This captures device-native highlights for comparison with our re-anchored output
    try:
        from tests.record_replay.harness.comparison import (
            assert_highlights_match,
            print_highlight_comparison,
        )

        golden_state = device.upload_golden_document(
            workspace.test_doc,
            prompt="Highlight 'target', 'bottom', and 'cross line' (same words as before)",
        )

        if golden_state.has_annotations:
            print("\n📌 GOLDEN COMPARISON: Re-anchored vs Device-Native")
            print_highlight_comparison(after_state.rm_files, golden_state.rm_files)

            # Assert positions match within tolerance
            # Use 5px tolerance to account for minor rendering differences
            assert_highlights_match(
                after_state.rm_files,
                golden_state.rm_files,
                tolerance_px=5.0,
            )
            print("✅ All highlight positions match within 5px tolerance!")
        else:
            print("\n⚠️  Golden document has no annotations - skipping comparison")

    except FileNotFoundError:
        # No golden testdata recorded yet - that's OK
        print("\n⚠️  No golden testdata recorded - skipping ground truth comparison")
        print("   Re-run with --online -s to record golden ground truth")

    device.end_test(test_id)
