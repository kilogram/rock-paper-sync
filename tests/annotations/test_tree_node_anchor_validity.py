"""Test that TreeNodeBlock anchors are valid after sync.

This test reproduces the bug where strokes are lost on the device due to
invalid TreeNodeBlock anchors. The device logs show:
    anchor=1:809 for group=2:772 is not present in text
    anchor=1:806 for group=2:763 is not present in text

The anchors exceed the page text length, causing the device to silently
drop the strokes.

Root cause: When markdown changes add text (positive delta), the delta
adjustment is incorrectly applied to TreeNodeBlocks that should be
excluded from the source page roundtrip.
"""

import io
from pathlib import Path

import pytest
import rmscene
from rmscene.scene_stream import RootTextBlock

from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.generator import RemarkableGenerator
from rock_paper_sync.parser import parse_markdown_file

# Path to multi_trip test data
TESTDATA_DIR = Path(__file__).parent.parent / "record_replay" / "testdata" / "multi_trip"


def get_page_text_length(rm_bytes: bytes) -> int:
    """Extract page text length from RootTextBlock in .rm file."""
    blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    for block in blocks:
        if isinstance(block, RootTextBlock):
            if hasattr(block, "value") and hasattr(block.value, "items"):
                text_parts = []
                for item_val in block.value.items.values():
                    if isinstance(item_val, str):
                        text_parts.append(item_val)
                return len("".join(text_parts))
    return 0


def get_user_tree_node_blocks(rm_bytes: bytes) -> list[tuple]:
    """Extract user-created TreeNodeBlocks (author ID 2) with their anchors.

    Returns:
        List of (node_id, anchor_offset) tuples
    """
    blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    result = []
    for block in blocks:
        if type(block).__name__ == "TreeNodeBlock":
            if hasattr(block, "group") and block.group:
                g = block.group
                if (
                    hasattr(g, "node_id")
                    and g.node_id
                    and g.node_id.part1 == 2  # User-created
                    and hasattr(g, "anchor_id")
                    and g.anchor_id
                ):
                    anchor = g.anchor_id.value.part2 if g.anchor_id.value else None
                    result.append((str(g.node_id), anchor))
    return result


def count_strokes(rm_bytes: bytes) -> int:
    """Count stroke blocks in .rm file."""
    blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    return len([b for b in blocks if "Line" in type(b).__name__])


@pytest.fixture
def multi_trip_testdata():
    """Load multi_trip test data if available."""
    phase_dir = TESTDATA_DIR / "phases" / "phase_2_phase_2"
    if not phase_dir.exists():
        pytest.skip("Multi-trip testdata not available")

    rm_dir = phase_dir / "rm_files"
    rm_files = sorted(rm_dir.glob("*.rm"))
    if not rm_files:
        pytest.skip("No .rm files in testdata")

    vault_snapshot = phase_dir / "vault_snapshot" / "document.md"
    if not vault_snapshot.exists():
        pytest.skip("No vault_snapshot markdown in testdata")

    return {
        "rm_files": rm_files,
        "markdown": vault_snapshot,
    }


def test_tree_node_anchors_within_page_bounds(multi_trip_testdata):
    """Test that all TreeNodeBlock anchors are within page text bounds.

    This test verifies that after generating .rm files from the sync process,
    all TreeNodeBlock anchor values are valid (within page text length).

    Invalid anchors cause the device to silently drop strokes, which is the
    bug we observed in device logs:
        anchor=1:809 for group=2:772 is not present in text

    The bug occurs when:
    1. .rm files have OLD text (e.g., without annotation markers)
    2. Markdown has NEW text (e.g., with annotation markers added)
    3. Delta (new_len - old_len) is positive
    4. Delta is incorrectly applied to cross-page TreeNodeBlocks
    """
    rm_files = multi_trip_testdata["rm_files"]
    markdown = multi_trip_testdata["markdown"]

    # Parse markdown
    md_doc = parse_markdown_file(markdown)

    # Generate with existing .rm files
    layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
    generator = RemarkableGenerator(layout)

    doc = generator.generate_document(
        md_doc,
        existing_page_uuids=[f.stem for f in rm_files],
        existing_rm_files=list(rm_files),
    )

    # Validate all TreeNodeBlock anchors are within page text bounds
    invalid_anchors = []
    total_tree_nodes = 0
    total_strokes = 0

    for i, page in enumerate(doc.pages):
        rm_bytes = generator.generate_rm_file(page)

        page_text_len = get_page_text_length(rm_bytes)
        tree_nodes = get_user_tree_node_blocks(rm_bytes)
        strokes = count_strokes(rm_bytes)

        total_tree_nodes += len(tree_nodes)
        total_strokes += strokes

        for node_id, anchor in tree_nodes:
            if anchor is None or anchor < 0 or anchor > page_text_len:
                invalid_anchors.append(
                    {
                        "page": i,
                        "node_id": node_id,
                        "anchor": anchor,
                        "page_text_len": page_text_len,
                    }
                )

    # Assert no invalid anchors
    if invalid_anchors:
        msg = "Invalid TreeNodeBlock anchors detected (would cause strokes to be lost on device):\n"
        for inv in invalid_anchors:
            msg += (
                f"  Page {inv['page']}: {inv['node_id']} anchor={inv['anchor']} "
                f"exceeds page_text_len={inv['page_text_len']}\n"
            )
        msg += "\nDevice would log: 'anchor=1:X for group=2:Y is not present in text'"
        pytest.fail(msg)

    # Also verify we didn't lose strokes in the process
    # Count input strokes
    input_strokes = 0
    for rm_file in rm_files:
        with open(rm_file, "rb") as f:
            input_strokes += count_strokes(f.read())

    assert (
        total_strokes >= input_strokes
    ), f"Strokes lost during generation: input={input_strokes}, output={total_strokes}"


def test_tree_node_anchor_delta_not_applied_to_cross_page():
    """Test that delta adjustment is NOT applied to cross-page TreeNodeBlocks.

    When TreeNodeBlocks move to a different page, they should get a new
    absolute anchor value for the target page, not the source page delta.

    This is the specific bug: TreeNodeBlocks are being processed with
    anchor_offset_delta from the source page roundtrip instead of being
    excluded and re-injected with absolute target page anchors.

    Repro scenario:
    - OLD text in .rm: 600 chars (no annotation markers)
    - NEW text in md: 771 chars (with annotation markers)
    - Delta: +171 chars
    - TreeNodeBlock anchor: 635 (valid for old text)
    - Bug: 635 + 171 = 806 (INVALID - exceeds new text length on target page)
    - Expected: absolute anchor for target page (e.g., 600)
    """
    phase_dir = TESTDATA_DIR / "phases" / "phase_2_phase_2"
    if not phase_dir.exists():
        pytest.skip("Multi-trip testdata not available")

    rm_dir = phase_dir / "rm_files"
    rm_files = sorted(rm_dir.glob("*.rm"))
    if not rm_files:
        pytest.skip("No .rm files in testdata")

    # Use Phase 0 markdown (original, without annotation markers) to create
    # a delta scenario. Phase 0 has shorter text.
    phase_0_dir = TESTDATA_DIR / "phases" / "phase_0_initial"
    original_md = phase_0_dir / "vault_snapshot" / "document.md"

    if not original_md.exists():
        pytest.skip("Phase 0 markdown not available")

    # Also get Phase 2 markdown (with annotation markers - longer text)
    phase_2_vault = TESTDATA_DIR / "phases" / "phase_2_phase_2" / "vault_snapshot" / "document.md"

    # Calculate expected delta
    original_content = original_md.read_text()
    phase_2_content = phase_2_vault.read_text()
    delta = len(phase_2_content) - len(original_content)

    if delta <= 0:
        pytest.skip(f"No positive delta in testdata (delta={delta})")

    # Now test: use Phase 2 .rm files (which have anchors valid for Phase 0/1 text)
    # and sync with Phase 2 markdown (longer text)
    # The TreeNodeBlocks should have VALID anchors, not anchor + delta

    md_doc = parse_markdown_file(phase_2_vault)
    layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
    generator = RemarkableGenerator(layout)

    doc = generator.generate_document(
        md_doc,
        existing_page_uuids=[f.stem for f in rm_files],
        existing_rm_files=list(rm_files),
    )

    # Check each output page
    for i, page in enumerate(doc.pages):
        rm_bytes = generator.generate_rm_file(page)
        page_text_len = get_page_text_length(rm_bytes)
        tree_nodes = get_user_tree_node_blocks(rm_bytes)

        for node_id, anchor in tree_nodes:
            # The bug would show anchors that are: original_anchor + delta
            # These would exceed page_text_len
            assert anchor is not None, f"Page {i}: TreeNodeBlock {node_id} has no anchor"
            assert anchor >= 0, f"Page {i}: TreeNodeBlock {node_id} has negative anchor {anchor}"
            assert anchor <= page_text_len, (
                f"Page {i}: TreeNodeBlock {node_id} anchor={anchor} exceeds "
                f"page_text_len={page_text_len}.\n"
                f"This is the delta bug: anchor appears to have delta (+{delta}) "
                f"incorrectly applied.\n"
                f"Device would reject this stroke with: "
                f"'anchor=1:{anchor} for group=... is not present in text'"
            )


def test_input_anchors_are_valid():
    """Sanity check: verify input .rm file anchors are valid for their text.

    This confirms the input data is correct - TreeNodeBlock anchors in the
    source .rm files should be valid for the text in those files.
    """
    phase_dir = TESTDATA_DIR / "phases" / "phase_2_phase_2"
    if not phase_dir.exists():
        pytest.skip("Multi-trip testdata not available")

    rm_dir = phase_dir / "rm_files"
    rm_files = sorted(rm_dir.glob("*.rm"))
    if not rm_files:
        pytest.skip("No .rm files in testdata")

    for rm_file in rm_files:
        with open(rm_file, "rb") as f:
            rm_bytes = f.read()

        page_text_len = get_page_text_length(rm_bytes)
        tree_nodes = get_user_tree_node_blocks(rm_bytes)

        for node_id, anchor in tree_nodes:
            assert anchor is not None, f"{rm_file.name}: TreeNodeBlock {node_id} has no anchor"
            assert 0 <= anchor <= page_text_len, (
                f"{rm_file.name}: TreeNodeBlock {node_id} anchor={anchor} "
                f"is invalid for page_text_len={page_text_len}"
            )


def test_anchor_validity_with_positive_delta():
    """Test anchors remain valid when markdown has MORE text than .rm files.

    This reproduces the exact device bug scenario:
    - .rm files have annotations with anchors valid for their text
    - Markdown is modified to have MORE text (positive delta)
    - After sync, TreeNodeBlock anchors must still be within page bounds

    The bug manifests when:
    1. Delta (new_len - old_len) is positive
    2. Delta is incorrectly applied to cross-page TreeNodeBlocks
    3. Result: anchor values exceed page_text_len
    4. Device silently drops strokes with message:
       "anchor=1:X for group=2:Y is not present in text"
    """
    phase_dir = TESTDATA_DIR / "phases" / "phase_2_phase_2"
    if not phase_dir.exists():
        pytest.skip("Multi-trip testdata not available")

    rm_dir = phase_dir / "rm_files"
    rm_files = sorted(rm_dir.glob("*.rm"))
    if not rm_files:
        pytest.skip("No .rm files in testdata")

    # Count input annotations to ensure we have test data
    input_tree_nodes = 0
    input_strokes = 0
    for rm_file in rm_files:
        with open(rm_file, "rb") as f:
            rm_bytes = f.read()
        input_tree_nodes += len(get_user_tree_node_blocks(rm_bytes))
        input_strokes += count_strokes(rm_bytes)

    if input_tree_nodes == 0:
        pytest.skip("No TreeNodeBlocks in testdata")

    # Read the original markdown and add extra text to create positive delta
    vault_snapshot = phase_dir / "vault_snapshot" / "document.md"
    if not vault_snapshot.exists():
        pytest.skip("No vault_snapshot markdown in testdata")

    original_content = vault_snapshot.read_text()

    # Add text to create positive delta - insert at strategic locations
    modified_content = original_content.replace(
        "Multi-Trip Annotation Test\n",
        "Multi-Trip Annotation Test\n\n[EXTRA TEXT INSERTED TO CREATE POSITIVE DELTA]\n",
    )
    modified_content = modified_content.replace(
        "Section 2:",
        "[MORE EXTRA TEXT FOR DELTA]\n\nSection 2:",
    )

    # Calculate delta
    delta = len(modified_content) - len(original_content)
    assert delta > 0, f"Expected positive delta, got {delta}"

    # Write modified markdown to temp file and parse it
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(modified_content)
        modified_md_path = Path(f.name)

    try:
        md_doc = parse_markdown_file(modified_md_path)

        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)

        doc = generator.generate_document(
            md_doc,
            existing_page_uuids=[f.stem for f in rm_files],
            existing_rm_files=list(rm_files),
        )

        # Validate all TreeNodeBlock anchors are within page text bounds
        invalid_anchors = []
        total_tree_nodes = 0
        total_strokes = 0

        for i, page in enumerate(doc.pages):
            rm_bytes = generator.generate_rm_file(page)

            page_text_len = get_page_text_length(rm_bytes)
            tree_nodes = get_user_tree_node_blocks(rm_bytes)
            strokes = count_strokes(rm_bytes)

            total_tree_nodes += len(tree_nodes)
            total_strokes += strokes

            for node_id, anchor in tree_nodes:
                if anchor is None or anchor < 0 or anchor > page_text_len:
                    invalid_anchors.append(
                        {
                            "page": i,
                            "node_id": node_id,
                            "anchor": anchor,
                            "page_text_len": page_text_len,
                            "delta": delta,
                        }
                    )

        # Assert no invalid anchors
        if invalid_anchors:
            msg = (
                f"Invalid TreeNodeBlock anchors detected with positive delta (+{delta} chars).\n"
                "This reproduces the device bug where strokes are lost:\n\n"
            )
            for inv in invalid_anchors:
                msg += (
                    f"  Page {inv['page']}: {inv['node_id']} anchor={inv['anchor']} "
                    f"exceeds page_text_len={inv['page_text_len']}\n"
                )
            msg += (
                f"\nThe delta (+{delta}) was likely incorrectly applied to "
                "TreeNodeBlocks that should have been excluded.\n"
                "Device would log: 'anchor=1:X for group=2:Y is not present in text'"
            )
            pytest.fail(msg)

        # Verify strokes weren't lost
        assert (
            total_strokes >= input_strokes
        ), f"Strokes lost during generation: input={input_strokes}, output={total_strokes}"

    finally:
        modified_md_path.unlink()
