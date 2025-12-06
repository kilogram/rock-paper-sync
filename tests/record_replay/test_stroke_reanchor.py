"""Test for stroke re-anchoring after document modification.

Strokes should move with their anchor paragraph when content changes.
Compares re-anchored positions against device-native ground truth.

Recording:
    uv run pytest tests/record_replay/test_stroke_reanchor.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_stroke_reanchor.py
"""

import io
import re

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations
from tests.record_replay.harness.comparison import (
    compare_strokes,
    print_stroke_comparison,
)


def count_strokes(rm_files: dict[str, bytes]) -> int:
    """Count total strokes across all .rm files."""
    count = 0
    for rm_data in rm_files.values():
        for anno in read_annotations(io.BytesIO(rm_data)):
            if anno.type == AnnotationType.STROKE:
                count += 1
    return count


@pytest.mark.device
def test_stroke_reanchor(device, workspace, fixtures_dir):
    """Strokes should re-anchor correctly when document content changes.

    Test flow:
    1. Upload document with strokes (margin notes, inline annotations)
    2. Insert new paragraph at beginning (pushes content down)
    3. Sync modified document
    4. Compare re-anchored stroke positions against device-native golden

    The golden document has strokes drawn directly on the modified text,
    representing the "correct" positions. Our re-anchoring should match.
    """
    fixture_doc = fixtures_dir / "test_stroke_reanchor.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(fixture_doc)
    except FileNotFoundError:
        pytest.skip("No testdata. Run with --online -s to record.")

    # Upload and get annotations
    doc_uuid = device.upload_document(workspace.test_doc)
    print("\n📝 Please add strokes:")
    print("   1. Write margin notes next to paragraphs")
    print("   2. Write something below 'End of document' (with a gap)")
    state = device.wait_for_annotations(doc_uuid)
    assert state.has_annotations, "Need strokes for re-anchoring test"

    initial_stroke_count = count_strokes(state.rm_files)
    assert initial_stroke_count >= 1, f"Need at least 1 stroke, got {initial_stroke_count}"
    print(f"\n📊 Initial: {initial_stroke_count} stroke(s)")

    # Modify document: insert a new paragraph at the beginning
    original = workspace.test_doc.read_text()

    # Check if document already has modifications (device-native capture mode)
    already_modified = "NEW PARAGRAPH" in original
    if not already_modified:
        # Insert new paragraph after title (handle annotation markers)
        title_pattern = r"(# Stroke Re-Anchor Test\n\n)(<!-- ANNOTATED:[^>]*-->\n)?"
        match = re.search(title_pattern, original)
        if match:
            insert_pos = match.end()
            modified = (
                original[:insert_pos]
                + "NEW PARAGRAPH: This text was inserted to push content down.\n\n"
                + original[insert_pos:]
            )
        else:
            modified = original.replace(
                "# Stroke Re-Anchor Test\n",
                "# Stroke Re-Anchor Test\n\nNEW PARAGRAPH: This text was inserted to push content down.\n",
                1,
            )
        workspace.test_doc.write_text(modified)
        print("✏️  Inserted new paragraph after title")

    # Sync modified document
    device.trigger_sync()
    device.capture_phase("post_modification", action="sync_modified")

    # Get re-anchored state
    after_state = device.get_document_state(doc_uuid)
    after_stroke_count = count_strokes(after_state.rm_files)

    assert (
        after_stroke_count == initial_stroke_count
    ), f"Stroke count changed: {initial_stroke_count} -> {after_stroke_count}"
    print(f"📊 After modification: {after_stroke_count} stroke(s) preserved")

    # Capture golden ground truth
    golden_state = device.upload_golden_document(
        workspace.test_doc,
        prompt=(
            "Add strokes in the SAME positions as they should appear:\n"
            "- Margin notes should be next to their paragraphs\n"
            "- Bottom stroke should be below 'End of document'"
        ),
    )

    if not golden_state.has_annotations:
        pytest.skip("Golden document has no strokes - cannot compare")

    golden_stroke_count = count_strokes(golden_state.rm_files)
    print(f"📊 Golden: {golden_stroke_count} stroke(s)")

    # Compare re-anchored vs golden
    print_stroke_comparison(after_state.rm_files, golden_state.rm_files)

    # Verify stroke positions match within tolerance
    # Note: Stroke bounding boxes are in native coordinates (relative to anchor),
    # not absolute coordinates. This means the comparison is approximate.
    # We check that MOST strokes match, allowing for outliers due to:
    # - Different anchor_origin_x values between documents
    # - Matching algorithm limitations with native coordinates
    result = compare_strokes(after_state.rm_files, golden_state.rm_files)

    # Count matches within tolerance
    good_matches = sum(1 for m in result.matches if m.distance <= 50.0)
    total_matches = len(result.matches)
    match_rate = good_matches / total_matches if total_matches > 0 else 0

    print(f"\n📊 Match quality: {good_matches}/{total_matches} ({match_rate:.0%}) within 50px")
    print(f"📊 Max delta: {result.max_delta_px:.1f}px")

    # Assert at least 80% of strokes match within tolerance
    # This accounts for native coordinate comparison limitations
    assert match_rate >= 0.80, (
        f"Too few strokes matched within 50px tolerance.\n"
        f"Expected: ≥80% ({int(total_matches * 0.80)} strokes)\n"
        f"Actual: {match_rate:.0%} ({good_matches} strokes)\n"
        f"This may indicate a stroke re-anchoring regression."
    )
    print(f"✅ {match_rate:.0%} of strokes match within 50px tolerance!")

    device.end_test(test_id)
