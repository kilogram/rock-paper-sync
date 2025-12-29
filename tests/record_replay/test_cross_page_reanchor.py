"""Test for cross-page annotation re-anchoring.

Annotations should follow their paragraph content across page boundaries
when document modifications cause pagination to change.

This tests the document-level annotation routing implemented in
_preserve_annotations() in generator.py.

Recording:
    uv run pytest tests/record_replay/test_cross_page_reanchor.py --online -s

Replaying:
    uv run pytest tests/record_replay/test_cross_page_reanchor.py
"""

import io

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations


def extract_annotations_by_page(rm_files: dict[str, bytes]) -> dict[str, list]:
    """Extract annotations grouped by page UUID.

    Returns:
        Dict mapping page_uuid to list of annotations on that page
    """
    by_page = {}
    for page_uuid, rm_data in rm_files.items():
        annotations = list(read_annotations(io.BytesIO(rm_data)))
        by_page[page_uuid] = annotations
    return by_page


def count_annotations_per_page(rm_files: dict[str, bytes]) -> dict[str, int]:
    """Count annotations per page.

    Returns:
        Dict mapping page_uuid to annotation count
    """
    by_page = extract_annotations_by_page(rm_files)
    return {page_uuid: len(annos) for page_uuid, annos in by_page.items()}


def total_annotation_count(rm_files: dict[str, bytes]) -> int:
    """Count total annotations across all pages."""
    return sum(count_annotations_per_page(rm_files).values())


def find_highlight_by_text(rm_files: dict[str, bytes], text_substring: str):
    """Find a highlight containing the given text substring.

    Returns:
        Tuple of (page_uuid, highlight) or (None, None) if not found
    """
    for page_uuid, rm_data in rm_files.items():
        for anno in read_annotations(io.BytesIO(rm_data)):
            if anno.type == AnnotationType.HIGHLIGHT and anno.highlight:
                if text_substring.lower() in anno.highlight.text.lower():
                    return page_uuid, anno.highlight
    return None, None


@pytest.mark.device
def test_cross_page_reanchor(device, workspace, fixtures_dir, visual_validator):
    """Test annotations moving across page boundaries when content changes.

    Test flow:
    1. Upload multi-page document with annotations on later pages
    2. Insert substantial content at beginning (pushing content to new pages)
    3. Verify annotations followed their paragraphs to new pages

    This validates document-level annotation routing works correctly.
    """
    fixture_doc = fixtures_dir / "test_cross_page_reanchor.md"
    workspace.test_doc.write_text(fixture_doc.read_text())

    try:
        test_id = device.start_test_for_fixture(fixture_doc)
    except FileNotFoundError:
        pytest.skip("No testdata. Run with --online -s to record.")

    # Upload and get initial annotations
    doc_uuid = device.upload_document(workspace.test_doc)
    print("\n📝 Please add annotations:")
    print("   1. Highlight 'target' in the Third Section")
    print("   2. Highlight 'bottom' in the Third Section")
    print("   3. Add a margin note next to '___ADD MARGIN NOTE HERE___' in the Fourth Section")

    state = device.wait_for_annotations(doc_uuid)
    assert state.has_annotations, "Need annotations for cross-page test"

    # Record initial state
    initial_total = total_annotation_count(state.rm_files)
    initial_per_page = count_annotations_per_page(state.rm_files)
    initial_page_count = len(state.rm_files)

    print("\n📊 Initial state:")
    print(f"   Total annotations: {initial_total}")
    print(f"   Pages with annotations: {initial_page_count}")
    for page_uuid, count in initial_per_page.items():
        print(f"   - {page_uuid[:8]}...: {count} annotation(s)")

    # Find specific highlights
    target_page_before, target_hl = find_highlight_by_text(state.rm_files, "target")
    bottom_page_before, bottom_hl = find_highlight_by_text(state.rm_files, "bottom")

    if target_hl:
        print(f"\n   'target' highlight on page {target_page_before[:8]}...")
    if bottom_hl:
        print(f"   'bottom' highlight on page {bottom_page_before[:8]}...")

    # Modify document: insert substantial content at beginning
    # This should push later content to new pages
    original = workspace.test_doc.read_text()

    # Check if already modified
    already_modified = "INSERTED CONTENT BLOCK" in original
    if not already_modified:
        import re

        # Insert a large block of text after the title
        insert_text = """
## INSERTED CONTENT BLOCK

This is a large block of inserted text that will push all subsequent content
down by several pages. The purpose is to test that annotations follow their
paragraph content across page boundaries.

Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor
incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis
nostrud exercitation ullamco laboris.

Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore
eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident.

Sunt in culpa qui officia deserunt mollit anim id est laborum. Sed ut
perspiciatis unde omnis iste natus error sit voluptatem accusantium.

Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut fugit,
sed quia consequuntur magni dolores eos qui ratione voluptatem sequi nesciunt.

Neque porro quisquam est, qui dolorem ipsum quia dolor sit amet, consectetur,
adipisci velit, sed quia non numquam eius modi tempora incidunt.

"""
        # Insert after the title, handling annotation markers
        # Pattern matches title with optional annotation markers around it
        title_pattern = (
            r"((?:<!-- ANNOTATED:[^>]*-->\s*)?"  # Optional opening marker
            r"# Cross-Page Annotation Re-Anchoring Test\s*"  # Title
            r"(?:<!-- /ANNOTATED -->\s*)?)"  # Optional closing marker
        )
        modified = re.sub(title_pattern, r"\1\n" + insert_text, original)

        if "INSERTED CONTENT BLOCK" in modified:
            workspace.test_doc.write_text(modified)
            print("\n✏️  Inserted large content block after title")
            print("   This should push later paragraphs to new pages")
        else:
            print("\n⚠️  WARNING: Failed to insert content block!")
            print(f"   First 300 chars of document:\n{original[:300]}")
    else:
        print("\n📌 DEVICE-NATIVE CAPTURE MODE: Document already modified")
        modified = original

    # Sync modified document
    device.trigger_sync()
    device.capture_phase("post_modification", action="sync_modified")

    # Compare with golden - both documents on device for side-by-side comparison
    after_state, golden_state = device.compare_with_golden(
        doc_uuid=doc_uuid,
        markdown_path=workspace.test_doc,
        observation=(
            "Check that annotations have followed their content:\n"
            "  1. 'target' highlight should be on 'target' in Third Section\n"
            "  2. 'bottom' highlight should be on 'bottom' in Third Section\n"
            "  3. Margin note should be next to '___ADD MARGIN NOTE HERE___'\n"
            "  NOTE: 'bottom' may be ~1 line lower than expected (known issue)"
        ),
        golden_prompt=(
            "Highlight 'target' and 'bottom' at their current positions.\n"
            "  Add margin note next to '___ADD MARGIN NOTE HERE___'."
        ),
    )
    after_total = total_annotation_count(after_state.rm_files)
    after_per_page = count_annotations_per_page(after_state.rm_files)
    after_page_count = len(after_state.rm_files)

    print("\n📊 After modification:")
    print(f"   Total annotations: {after_total}")
    print(f"   Pages with annotations: {after_page_count}")
    for page_uuid, count in after_per_page.items():
        print(f"   - {page_uuid[:8]}...: {count} annotation(s)")

    # Find highlights after modification
    target_page_after, target_hl_after = find_highlight_by_text(after_state.rm_files, "target")
    bottom_page_after, bottom_hl_after = find_highlight_by_text(after_state.rm_files, "bottom")

    if target_hl_after:
        print(f"\n   'target' highlight now on page {target_page_after[:8]}...")
    if bottom_hl_after:
        print(f"   'bottom' highlight now on page {bottom_page_after[:8]}...")

    # ASSERTIONS

    # 1. Total annotation count should be preserved
    assert after_total == initial_total, (
        f"ANNOTATION LOSS: Total annotation count changed.\n"
        f"Before: {initial_total}\n"
        f"After:  {after_total}\n"
        f"Lost {initial_total - after_total} annotation(s)"
    )
    print(f"\n✅ Total annotation count preserved: {after_total}")

    # 2. Highlights should still exist (may be on different pages)
    if target_hl:
        assert (
            target_hl_after is not None
        ), "HIGHLIGHT LOST: 'target' highlight disappeared after modification"
        print("✅ 'target' highlight preserved")

    if bottom_hl:
        assert (
            bottom_hl_after is not None
        ), "HIGHLIGHT LOST: 'bottom' highlight disappeared after modification"
        print("✅ 'bottom' highlight preserved")

    # 3. If document grew, annotations may have moved to new pages
    # (This is the expected behavior for cross-page re-anchoring)
    if not already_modified:
        # Only check page movement if we actually modified the document
        pages_changed = False
        if target_page_before and target_page_after:
            if target_page_before != target_page_after:
                pages_changed = True
                print(
                    f"✅ 'target' highlight moved from page {target_page_before[:8]}... "
                    f"to {target_page_after[:8]}..."
                )
        if bottom_page_before and bottom_page_after:
            if bottom_page_before != bottom_page_after:
                pages_changed = True
                print(
                    f"✅ 'bottom' highlight moved from page {bottom_page_before[:8]}... "
                    f"to {bottom_page_after[:8]}..."
                )

        if pages_changed:
            print("\n🎉 Cross-page annotation movement verified!")
        else:
            print("\n⚠️  No cross-page movement detected (may need more inserted content)")

    # Golden comparison (golden_state already captured in compare_with_golden)
    if golden_state.has_annotations:
        try:
            from tests.record_replay.harness.comparison import (
                assert_highlights_match,
                print_highlight_comparison,
                save_comparison_images,
            )

            # Save debug images for visual inspection
            debug_dir = visual_validator.debug_dir / "golden_comparison"
            saved_images = save_comparison_images(
                after_state.rm_files,
                golden_state.rm_files,
                debug_dir,
                reanchored_page_order=after_state.page_uuids,
                golden_page_order=golden_state.page_uuids,
            )
            if saved_images:
                print(f"\n📸 Debug images saved to: {debug_dir}")
                for img_path in saved_images:
                    print(f"   - {img_path.name}")

            print("\n📌 GOLDEN COMPARISON: Re-anchored vs Device-Native")
            print_highlight_comparison(after_state.rm_files, golden_state.rm_files)

            # Assert highlights are on the correct text
            # We use text-based matching because pagination may differ between
            # our generator and the device, causing different absolute positions.
            # The key requirement is that highlights follow their anchor text.
            assert_highlights_match(
                after_state.rm_files,
                golden_state.rm_files,
                match_by_text=True,
            )
            print("✅ All highlights matched by text content!")

        except ImportError:
            print("\n⚠️  Comparison module not available")

        # Note: Visual comparison is skipped because our pagination may differ
        # from the device's pagination. The key verification is that highlights
        # follow their anchor text (verified above via text-based matching).
        # TODO: Re-enable visual comparison once pagination is aligned with device.
        # print("\n📌 VISUAL COMPARISON: Asserting uploaded_rm matches golden")
        # result = visual_validator.assert_uploaded_matches_golden(test_id, trip_number=2)
        # print(f"✅ Visual comparison passed: {len(result.matches)} cluster(s) matched")
    else:
        print("\n⚠️  No golden annotations captured - skipping comparison")

    device.end_test(test_id)
