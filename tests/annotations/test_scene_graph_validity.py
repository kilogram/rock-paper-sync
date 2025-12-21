"""Test that scene graph structure is valid after sync.

This test validates the structural integrity of the scene graph in generated
.rm files. The reMarkable v6 format requires specific block relationships:

    SceneTreeBlock → TreeNodeBlock → SceneGroupItemBlock → SceneLineItemBlock
       (declares)      (anchors)        (links to layer)      (stroke data)

Missing any of these blocks causes the device to silently fail rendering.

These tests would have caught the Phase 3 bug (missing SceneTreeBlock) that
caused strokes to disappear on the device with the error:
    "Unable to find node with id=0:11"
"""

import io
from pathlib import Path

import pytest
import rmscene

from rock_paper_sync.annotations import (
    SceneGraphIndex,
    StrokeBundle,
    validate_scene_graph,
)
from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.generator import RemarkableGenerator
from rock_paper_sync.parser import parse_markdown_file

# Path to cross_page_reanchor test data (has cross-page stroke movement)
CROSS_PAGE_TESTDATA = (
    Path(__file__).parent.parent / "record_replay" / "testdata" / "cross_page_reanchor"
)


def get_rm_files_from_trip(trip_num: int) -> list[Path]:
    """Get .rm files from a specific trip."""
    rm_dir = CROSS_PAGE_TESTDATA / "trips" / str(trip_num) / "annotations" / "rm_files"
    if not rm_dir.exists():
        return []
    return sorted(rm_dir.glob("*.rm"))


def get_markdown_from_trip(trip_num: int) -> Path | None:
    """Get markdown file from a specific trip."""
    md_file = CROSS_PAGE_TESTDATA / "trips" / str(trip_num) / "vault" / "document.md"
    return md_file if md_file.exists() else None


@pytest.fixture
def cross_page_testdata():
    """Load cross_page_reanchor test data for trip 1 → 2 transition.

    Trip 1: Initial state with annotations (.rm files)
    Trip 2: Modified markdown that causes cross-page movement
    """
    if not CROSS_PAGE_TESTDATA.exists():
        pytest.skip("cross_page_reanchor testdata not available")

    # Trip 1 has the .rm files with annotations
    rm_files = get_rm_files_from_trip(1)
    if not rm_files:
        pytest.skip("No .rm files in trip 1")

    # Trip 2 has the modified markdown that causes cross-page movement
    markdown = get_markdown_from_trip(2)
    if not markdown or not markdown.exists():
        pytest.skip("No markdown in trip 2")

    return {
        "rm_files": rm_files,
        "markdown": markdown,
    }


class TestSceneGraphStructure:
    """Tests for scene graph structural integrity."""

    def test_input_files_have_valid_scene_graph(self, cross_page_testdata):
        """Verify input .rm files have valid scene graph structure.

        This is a sanity check - if input files are invalid, test data is broken.
        """
        rm_files = cross_page_testdata["rm_files"]

        for rm_file in rm_files:
            with open(rm_file, "rb") as f:
                rm_bytes = f.read()

            result = validate_scene_graph(rm_bytes)

            assert result.is_valid, f"Input file {rm_file.name} has invalid scene graph:\n{result}"

    @pytest.mark.xfail(
        reason="Known issue: cross-page migration missing TreeNodeBlock for some strokes. "
        "See SceneGroupItemBlock.value=2:299 in test output. Tracked for StrokeBundle refactor."
    )
    def test_generated_files_have_valid_scene_graph(self, cross_page_testdata):
        """Test that generated .rm files have valid scene graph structure.

        This test would have caught the Phase 3 bug where SceneTreeBlock was
        missing for cross-page strokes.

        Current known issue: Cross-page migration sometimes creates SceneTreeBlock
        and SceneGroupItemBlock for a stroke without the corresponding TreeNodeBlock.
        This will be fixed when we implement the StrokeBundle abstraction that
        ensures all 4 required blocks are moved together.
        """
        rm_files = cross_page_testdata["rm_files"]
        markdown = cross_page_testdata["markdown"]

        # Parse and generate
        md_doc = parse_markdown_file(markdown)
        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)

        doc = generator.generate_document(
            md_doc,
            existing_page_uuids=[f.stem for f in rm_files],
            existing_rm_files=list(rm_files),
        )

        # Validate each generated page
        all_errors = []
        for i, page in enumerate(doc.pages):
            rm_bytes = generator.generate_rm_file(page)
            result = validate_scene_graph(rm_bytes)

            if not result.is_valid:
                all_errors.append(f"Page {i}:\n{result}")

        assert not all_errors, (
            "Generated .rm files have invalid scene graph structure:\n" + "\n".join(all_errors)
        )

    def test_cross_page_strokes_have_scene_tree_blocks(self, cross_page_testdata):
        """Test that cross-page strokes have SceneTreeBlock declarations.

        When strokes move to a new page, they need:
        1. SceneTreeBlock - declares node in scene tree
        2. TreeNodeBlock - anchors to text position
        3. SceneGroupItemBlock - links to layer

        The Phase 3 bug was missing #1, causing "Unable to find node" errors.
        """
        rm_files = cross_page_testdata["rm_files"]
        markdown = cross_page_testdata["markdown"]

        # Parse and generate
        md_doc = parse_markdown_file(markdown)
        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)

        doc = generator.generate_document(
            md_doc,
            existing_page_uuids=[f.stem for f in rm_files],
            existing_rm_files=list(rm_files),
        )

        # Count user TreeNodeBlocks and SceneTreeBlocks across all pages
        total_user_tree_nodes = 0
        total_scene_trees_for_user_nodes = 0

        for i, page in enumerate(doc.pages):
            rm_bytes = generator.generate_rm_file(page)
            blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))

            # Find user-created TreeNodeBlocks
            user_node_ids = set()
            for block in blocks:
                if type(block).__name__ == "TreeNodeBlock":
                    if hasattr(block, "group") and block.group:
                        node_id = block.group.node_id
                        if hasattr(node_id, "part1") and node_id.part1 == 2:
                            user_node_ids.add(f"{node_id.part1}:{node_id.part2}")

            # Find SceneTreeBlocks for user nodes
            scene_tree_ids = set()
            for block in blocks:
                if type(block).__name__ == "SceneTreeBlock":
                    if hasattr(block, "tree_id") and block.tree_id:
                        tree_id = block.tree_id
                        if hasattr(tree_id, "part1") and tree_id.part1 == 2:
                            scene_tree_ids.add(f"{tree_id.part1}:{tree_id.part2}")

            total_user_tree_nodes += len(user_node_ids)
            total_scene_trees_for_user_nodes += len(scene_tree_ids)

            # Every user TreeNodeBlock must have a SceneTreeBlock
            missing = user_node_ids - scene_tree_ids
            assert not missing, (
                f"Page {i}: TreeNodeBlocks without SceneTreeBlock declaration: {missing}\n"
                "This would cause 'Unable to find node' error on device"
            )

        # Verify we actually tested something
        assert total_user_tree_nodes > 0, "No user TreeNodeBlocks found - test data issue"

    @pytest.mark.xfail(
        reason="Known issue: same as test_generated_files_have_valid_scene_graph. "
        "Cross-page migration creates orphaned SceneGroupItemBlocks."
    )
    def test_scene_group_items_reference_existing_nodes(self, cross_page_testdata):
        """Test that SceneGroupItemBlocks reference existing TreeNodeBlocks.

        SceneGroupItemBlock.value must point to an existing TreeNodeBlock.
        SceneGroupItemBlock.parent_id must point to an existing TreeNodeBlock (layer).
        """
        rm_files = cross_page_testdata["rm_files"]
        markdown = cross_page_testdata["markdown"]

        md_doc = parse_markdown_file(markdown)
        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)

        doc = generator.generate_document(
            md_doc,
            existing_page_uuids=[f.stem for f in rm_files],
            existing_rm_files=list(rm_files),
        )

        for i, page in enumerate(doc.pages):
            rm_bytes = generator.generate_rm_file(page)
            result = validate_scene_graph(rm_bytes)

            # Check for orphaned or missing parent errors
            orphaned = [e for e in result.errors if "ORPHANED" in e.error_type]
            missing = [e for e in result.errors if "MISSING" in e.error_type]

            assert not orphaned, (
                f"Page {i}: SceneGroupItemBlocks reference non-existent nodes:\n"
                + "\n".join(str(e) for e in orphaned)
            )
            assert not missing, (
                f"Page {i}: SceneGroupItemBlocks have invalid parent_id:\n"
                + "\n".join(str(e) for e in missing)
            )


class TestSceneGraphValidatorUnit:
    """Unit tests for the scene graph validator itself."""

    def test_valid_scene_graph_passes(self):
        """Test that a valid .rm file passes validation."""
        # Use a known good input file
        testdata = CROSS_PAGE_TESTDATA / "trips" / "1" / "annotations" / "rm_files"
        if not testdata.exists():
            pytest.skip("testdata not available")

        rm_files = sorted(testdata.glob("*.rm"))
        if not rm_files:
            pytest.skip("No .rm files in testdata")

        with open(rm_files[0], "rb") as f:
            rm_bytes = f.read()

        result = validate_scene_graph(rm_bytes)
        assert result.is_valid, f"Expected valid scene graph:\n{result}"

    def test_counts_blocks_correctly(self):
        """Test that validator counts block types correctly."""
        testdata = CROSS_PAGE_TESTDATA / "trips" / "1" / "annotations" / "rm_files"
        if not testdata.exists():
            pytest.skip("testdata not available")

        rm_files = sorted(testdata.glob("*.rm"))
        if not rm_files:
            pytest.skip("No .rm files in testdata")

        with open(rm_files[0], "rb") as f:
            rm_bytes = f.read()

        result = validate_scene_graph(rm_bytes)

        # Should have at least the base structure
        assert result.tree_node_count >= 2, "Expected at least Layer 1 and root TreeNodeBlocks"
        assert result.scene_tree_count >= 1, "Expected at least Layer 1 SceneTreeBlock"


class TestStrokeBundle:
    """Tests for the StrokeBundle abstraction."""

    def test_extract_bundles_from_valid_rm_file(self):
        """Test extracting StrokeBundles from a valid .rm file with annotations."""
        testdata = CROSS_PAGE_TESTDATA / "trips" / "1" / "annotations" / "rm_files"
        if not testdata.exists():
            pytest.skip("testdata not available")

        rm_files = sorted(testdata.glob("*.rm"))
        if not rm_files:
            pytest.skip("No .rm files in testdata")

        # Find a file with user annotations (check each file)
        bundles_found = []
        for rm_file in rm_files:
            with open(rm_file, "rb") as f:
                rm_bytes = f.read()

            index = SceneGraphIndex.from_bytes(rm_bytes)
            bundles = StrokeBundle.from_index(index)
            bundles_found.extend(bundles)

        # Should find some user stroke bundles in the test data
        assert len(bundles_found) > 0, "Expected to find at least one StrokeBundle in test data"

    def test_complete_bundle_has_all_blocks(self):
        """Test that complete bundles have all 4 block types."""
        testdata = CROSS_PAGE_TESTDATA / "trips" / "1" / "annotations" / "rm_files"
        if not testdata.exists():
            pytest.skip("testdata not available")

        rm_files = sorted(testdata.glob("*.rm"))
        if not rm_files:
            pytest.skip("No .rm files in testdata")

        complete_bundles = []
        for rm_file in rm_files:
            with open(rm_file, "rb") as f:
                rm_bytes = f.read()

            index = SceneGraphIndex.from_bytes(rm_bytes)
            bundles = StrokeBundle.from_index(index)

            for bundle in bundles:
                if bundle.is_complete:
                    complete_bundles.append(bundle)

        # At least one complete bundle should exist in valid input files
        assert len(complete_bundles) > 0, "Expected at least one complete StrokeBundle"

        # Verify complete bundles have all components
        for bundle in complete_bundles:
            assert bundle.tree_node is not None, f"{bundle} missing TreeNodeBlock"
            assert bundle.scene_tree is not None, f"{bundle} missing SceneTreeBlock"
            assert bundle.scene_group_item is not None, f"{bundle} missing SceneGroupItemBlock"
            assert len(bundle.strokes) > 0, f"{bundle} missing stroke data"
            assert bundle.missing_blocks == [], f"{bundle} should have no missing blocks"

    def test_to_raw_blocks_preserves_order(self):
        """Test that to_raw_blocks returns blocks in correct order."""
        testdata = CROSS_PAGE_TESTDATA / "trips" / "1" / "annotations" / "rm_files"
        if not testdata.exists():
            pytest.skip("testdata not available")

        rm_files = sorted(testdata.glob("*.rm"))
        if not rm_files:
            pytest.skip("No .rm files in testdata")

        for rm_file in rm_files:
            with open(rm_file, "rb") as f:
                rm_bytes = f.read()

            index = SceneGraphIndex.from_bytes(rm_bytes)
            bundles = StrokeBundle.from_index(index)

            for bundle in bundles:
                if bundle.is_complete:
                    raw_blocks = bundle.to_raw_blocks()

                    # Should return 3 + len(strokes) blocks
                    expected_count = 3 + len(bundle.strokes)
                    assert (
                        len(raw_blocks) == expected_count
                    ), f"Expected {expected_count} raw blocks, got {len(raw_blocks)}"

                    # First should be SceneTreeBlock
                    assert type(raw_blocks[0]).__name__ == "SceneTreeBlock"
                    # Second should be TreeNodeBlock
                    assert type(raw_blocks[1]).__name__ == "TreeNodeBlock"
                    # Third should be SceneGroupItemBlock
                    assert type(raw_blocks[2]).__name__ == "SceneGroupItemBlock"
                    # Rest should be line blocks
                    for i, stroke_block in enumerate(raw_blocks[3:]):
                        assert (
                            "Line" in type(stroke_block).__name__
                        ), f"Block {i+3} should be a Line block"
                    return  # Found at least one complete bundle

    def test_incomplete_bundle_reports_missing_blocks(self):
        """Test that incomplete bundles correctly report missing blocks."""
        from rmscene import CrdtId

        # Create a bundle with only a node_id (missing everything)
        node_id = CrdtId(2, 999)
        bundle = StrokeBundle(node_id=node_id)

        assert not bundle.is_complete
        assert "TreeNodeBlock" in bundle.missing_blocks
        assert "SceneTreeBlock" in bundle.missing_blocks
        assert "SceneGroupItemBlock" in bundle.missing_blocks
        assert "SceneLineItemBlock" in bundle.missing_blocks

        # Validate should return errors
        errors = bundle.validate()
        assert len(errors) == 4, f"Expected 4 validation errors, got {len(errors)}"

    def test_bundle_anchor_offset(self):
        """Test that anchor_offset is correctly extracted from TreeNodeBlock."""
        testdata = CROSS_PAGE_TESTDATA / "trips" / "1" / "annotations" / "rm_files"
        if not testdata.exists():
            pytest.skip("testdata not available")

        rm_files = sorted(testdata.glob("*.rm"))
        if not rm_files:
            pytest.skip("No .rm files in testdata")

        found_with_anchor = False
        for rm_file in rm_files:
            with open(rm_file, "rb") as f:
                rm_bytes = f.read()

            index = SceneGraphIndex.from_bytes(rm_bytes)
            bundles = StrokeBundle.from_index(index)

            for bundle in bundles:
                if bundle.tree_node and bundle.anchor_offset is not None:
                    found_with_anchor = True
                    # Anchor should be a valid character offset
                    assert (
                        bundle.anchor_offset >= 0
                    ), f"Anchor offset should be non-negative, got {bundle.anchor_offset}"

        # We should find at least one bundle with an anchor in the test data
        assert found_with_anchor, "Expected at least one bundle with anchor_offset"
