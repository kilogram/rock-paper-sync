"""Test scene graph validity for cross-page annotation migration.

This test validates that when annotations move across pages during content
modification, the generated .rm files have valid scene graph structures.

The reMarkable v6 CRDT scene graph requires 4 interdependent blocks per stroke:
    1. SceneTreeBlock - declares the node exists in the scene tree
    2. TreeNodeBlock - defines the node and its text anchor
    3. SceneGroupItemBlock - links the node to its parent layer
    4. SceneLineItemBlock - contains the actual stroke data

If any of these blocks are missing or incorrectly linked, the device will
fail to render the strokes (they "disappear").
"""

import io
from pathlib import Path

import pytest
import rmscene
from rmscene import (
    SceneGlyphItemBlock,
    SceneGroupItemBlock,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)

from rock_paper_sync.annotations.document_model import DocumentModel
from rock_paper_sync.annotations.scene_graph import (
    SceneGraphIndex,
    StrokeBundle,
    validate_scene_graph,
)
from rock_paper_sync.annotations.services.merger import AnnotationMerger, MergeContext
from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.generator import RemarkableGenerator
from rock_paper_sync.layout import PAPER_PRO_MOVE
from rock_paper_sync.parser import parse_markdown_file

# Test data paths
TESTDATA_DIR = Path(__file__).parent.parent / "record_replay" / "testdata" / "cross_page_reanchor"
TRIP1_DIR = TESTDATA_DIR / "trips" / "1"
TRIP2_DIR = TESTDATA_DIR / "trips" / "2"
GOLDEN_DIR = TESTDATA_DIR / "trips" / "golden"
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def count_blocks(rm_bytes: bytes) -> dict:
    """Count different block types in an .rm file."""
    blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    return {
        "strokes": len([b for b in blocks if isinstance(b, SceneLineItemBlock)]),
        "highlights": len([b for b in blocks if isinstance(b, SceneGlyphItemBlock)]),
        "tree_nodes": len([b for b in blocks if isinstance(b, TreeNodeBlock)]),
        "scene_trees": len([b for b in blocks if isinstance(b, SceneTreeBlock)]),
        "scene_group_items": len([b for b in blocks if isinstance(b, SceneGroupItemBlock)]),
    }


def get_user_tree_nodes(rm_bytes: bytes) -> list[TreeNodeBlock]:
    """Get user-created TreeNodeBlocks (part1 == 2)."""
    blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    return [
        b
        for b in blocks
        if isinstance(b, TreeNodeBlock)
        and hasattr(b, "group")
        and b.group
        and b.group.node_id.part1 == 2
    ]


def get_user_scene_trees(rm_bytes: bytes) -> list[SceneTreeBlock]:
    """Get user-created SceneTreeBlocks (tree_id.part1 == 2)."""
    blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    return [b for b in blocks if isinstance(b, SceneTreeBlock) and b.tree_id.part1 == 2]


def get_user_scene_group_items(rm_bytes: bytes) -> list[SceneGroupItemBlock]:
    """Get user-created SceneGroupItemBlocks (value.part1 == 2)."""
    blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    return [b for b in blocks if isinstance(b, SceneGroupItemBlock) and b.item.value.part1 == 2]


@pytest.fixture
def trip1_rm_files() -> list[Path]:
    """Get the .rm files from trip 1 (initial annotations)."""
    rm_dir = TRIP1_DIR / "annotations" / "rm_files"
    return list(sorted(rm_dir.glob("*.rm")))


@pytest.fixture
def page_uuids(trip1_rm_files) -> list[str]:
    """Get page UUIDs from trip 1 .rm file names."""
    return [p.stem for p in trip1_rm_files]


@pytest.fixture
def old_markdown() -> Path:
    """Get the original (unmodified) markdown file."""
    return FIXTURES_DIR / "test_cross_page_reanchor.md"


@pytest.fixture
def new_markdown() -> Path:
    """Get the modified markdown file (with inserted content)."""
    return TRIP2_DIR / "vault" / "document.md"


@pytest.fixture
def generator():
    """Create a RemarkableGenerator with default config."""
    layout_config = LayoutConfig()
    return RemarkableGenerator(layout_config, PAPER_PRO_MOVE)


class TestInputValidation:
    """Validate the input testdata has correct scene graph structure."""

    def test_trip1_scene_graphs_are_valid(self, trip1_rm_files):
        """Trip 1 .rm files should have valid scene graphs."""
        for rm_path in trip1_rm_files:
            with open(rm_path, "rb") as f:
                rm_bytes = f.read()

            result = validate_scene_graph(rm_bytes)
            assert result.is_valid, (
                f"Trip 1 file {rm_path.name} has invalid scene graph:\n"
                + "\n".join(str(e) for e in result.errors)
            )

    def test_trip1_has_expected_annotations(self, trip1_rm_files):
        """Trip 1 should have 18 strokes + 2 highlights = 20 annotations."""
        total_strokes = 0
        total_highlights = 0

        for rm_path in trip1_rm_files:
            with open(rm_path, "rb") as f:
                counts = count_blocks(f.read())
            total_strokes += counts["strokes"]
            total_highlights += counts["highlights"]

        assert total_strokes == 17, f"Expected 17 strokes, got {total_strokes}"
        assert total_highlights == 2, f"Expected 2 highlights, got {total_highlights}"

    def test_trip1_stroke_bundles_are_complete(self, trip1_rm_files):
        """All stroke bundles in trip 1 should have all 4 required blocks."""
        for rm_path in trip1_rm_files:
            with open(rm_path, "rb") as f:
                rm_bytes = f.read()

            index = SceneGraphIndex.from_bytes(rm_bytes)
            bundles = StrokeBundle.from_index(index)

            for bundle in bundles:
                assert (
                    bundle.is_complete
                ), f"Trip 1 file {rm_path.name} has incomplete bundle: {bundle}"


class TestDocumentModelMigration:
    """Test that DocumentModel correctly migrates annotations."""

    def test_migration_preserves_annotation_count(self, trip1_rm_files, page_uuids, new_markdown):
        """Migration should preserve total annotation count."""
        # Load old model
        old_model = DocumentModel.from_rm_files(
            rm_files=trip1_rm_files,
            geometry=PAPER_PRO_MOVE,
        )
        initial_count = len(old_model.annotations)

        # Create new model and migrate
        new_md_doc = parse_markdown_file(new_markdown)
        new_model = DocumentModel.from_content_blocks(new_md_doc.content, PAPER_PRO_MOVE)
        merger = AnnotationMerger(fuzzy_threshold=0.8)
        context = MergeContext(old_model=old_model, new_model=new_model)
        result = merger.merge(context)
        new_model = result.merged_model
        report = result.report

        assert len(new_model.annotations) == initial_count, (
            f"Migration changed annotation count from {initial_count} to {len(new_model.annotations)}\n"
            f"Migrations: {len(report.migrations)}, Orphans: {len(report.orphans)}"
        )

    def test_migration_preserves_scene_graph_blocks(self, trip1_rm_files, page_uuids, new_markdown):
        """Migrated annotations should retain their scene graph blocks."""
        # Load old model
        old_model = DocumentModel.from_rm_files(
            rm_files=trip1_rm_files,
            geometry=PAPER_PRO_MOVE,
        )

        # Create new model and migrate
        new_md_doc = parse_markdown_file(new_markdown)
        new_model = DocumentModel.from_content_blocks(new_md_doc.content, PAPER_PRO_MOVE)
        merger = AnnotationMerger(fuzzy_threshold=0.8)
        context = MergeContext(old_model=old_model, new_model=new_model)
        result = merger.merge(context)
        new_model = result.merged_model

        # Check stroke annotations have all required blocks
        for anno in new_model.annotations:
            if anno.annotation_type == "stroke":
                assert anno.original_tree_node is not None, "Stroke missing TreeNodeBlock"
                assert anno.original_scene_tree_block is not None, "Stroke missing SceneTreeBlock"
                assert (
                    anno.original_scene_group_item is not None
                ), "Stroke missing SceneGroupItemBlock"


class TestGeneratedRmFileValidity:
    """Test that generated .rm files have valid scene graphs."""

    def test_generated_rm_files_are_valid(
        self, trip1_rm_files, page_uuids, new_markdown, generator
    ):
        """All generated .rm files should have valid scene graphs."""
        new_md_doc = parse_markdown_file(new_markdown)

        rm_doc = generator.generate_document(
            md_doc=new_md_doc,
            doc_uuid="test-doc-uuid",
            parent_uuid="",
            existing_page_uuids=page_uuids,
            existing_rm_files=trip1_rm_files,
        )

        # Validate each generated page
        all_errors = []
        for i, page in enumerate(rm_doc.pages):
            rm_bytes = generator.generate_rm_file(page)
            result = validate_scene_graph(rm_bytes)

            if not result.is_valid:
                all_errors.append(f"Page {i} ({page.uuid[:12]}...):")
                for e in result.errors:
                    all_errors.append(f"  {e}")

        assert not all_errors, "Scene graph validation errors:\n" + "\n".join(all_errors)

    def test_generated_rm_files_preserve_annotation_count(
        self, trip1_rm_files, page_uuids, new_markdown, generator
    ):
        """Generated .rm files should have same total annotation count as input."""
        # Count input annotations
        input_strokes = 0
        input_highlights = 0
        for rm_path in trip1_rm_files:
            with open(rm_path, "rb") as f:
                counts = count_blocks(f.read())
            input_strokes += counts["strokes"]
            input_highlights += counts["highlights"]

        # Generate document
        new_md_doc = parse_markdown_file(new_markdown)

        rm_doc = generator.generate_document(
            md_doc=new_md_doc,
            doc_uuid="test-doc-uuid",
            parent_uuid="",
            existing_page_uuids=page_uuids,
            existing_rm_files=trip1_rm_files,
        )

        # Count output annotations
        output_strokes = 0
        output_highlights = 0
        for page in rm_doc.pages:
            rm_bytes = generator.generate_rm_file(page)
            counts = count_blocks(rm_bytes)
            output_strokes += counts["strokes"]
            output_highlights += counts["highlights"]

        assert (
            output_strokes == input_strokes
        ), f"Stroke count changed: {input_strokes} -> {output_strokes}"
        assert (
            output_highlights == input_highlights
        ), f"Highlight count changed: {input_highlights} -> {output_highlights}"

    def test_stroke_bundles_are_complete_after_generation(
        self, trip1_rm_files, page_uuids, new_markdown, generator
    ):
        """All stroke bundles in generated .rm files should be complete."""
        new_md_doc = parse_markdown_file(new_markdown)

        rm_doc = generator.generate_document(
            md_doc=new_md_doc,
            doc_uuid="test-doc-uuid",
            parent_uuid="",
            existing_page_uuids=page_uuids,
            existing_rm_files=trip1_rm_files,
        )

        # Check each generated page
        incomplete_bundles = []
        for i, page in enumerate(rm_doc.pages):
            rm_bytes = generator.generate_rm_file(page)
            index = SceneGraphIndex.from_bytes(rm_bytes)
            bundles = StrokeBundle.from_index(index)

            for bundle in bundles:
                if not bundle.is_complete:
                    incomplete_bundles.append(f"Page {i}: {bundle}")

        assert not incomplete_bundles, "Incomplete stroke bundles found:\n" + "\n".join(
            incomplete_bundles
        )


class TestCrossPageInjection:
    """Test cross-page annotation injection specifically."""

    def test_from_scratch_page_has_valid_scene_graph(
        self, trip1_rm_files, page_uuids, new_markdown, generator
    ):
        """Pages generated from scratch (new UUIDs) should have valid scene graphs.

        This is the critical case where strokes were disappearing - when content
        moves to a page that doesn't have a matching source .rm file, the page
        uses FROM-SCRATCH generation path which must correctly inject all 4
        required blocks for each stroke.
        """
        new_md_doc = parse_markdown_file(new_markdown)

        rm_doc = generator.generate_document(
            md_doc=new_md_doc,
            doc_uuid="test-doc-uuid",
            parent_uuid="",
            existing_page_uuids=page_uuids,
            existing_rm_files=trip1_rm_files,
        )

        # Find pages with new UUIDs (FROM-SCRATCH generation)
        existing_uuids = set(page_uuids)
        from_scratch_pages = [
            (i, page) for i, page in enumerate(rm_doc.pages) if page.uuid not in existing_uuids
        ]

        if not from_scratch_pages:
            pytest.skip("No FROM-SCRATCH pages generated in this test")

        # Validate FROM-SCRATCH pages
        for i, page in from_scratch_pages:
            rm_bytes = generator.generate_rm_file(page)
            result = validate_scene_graph(rm_bytes)

            assert result.is_valid, (
                f"FROM-SCRATCH page {i} ({page.uuid[:12]}...) has invalid scene graph:\n"
                + "\n".join(str(e) for e in result.errors)
            )

            # Check bundles are complete
            index = SceneGraphIndex.from_bytes(rm_bytes)
            bundles = StrokeBundle.from_index(index)
            incomplete = [b for b in bundles if not b.is_complete]

            assert not incomplete, f"FROM-SCRATCH page {i} has incomplete bundles:\n" + "\n".join(
                str(b) for b in incomplete
            )

    def test_cross_page_strokes_have_correct_tree_node_ids(
        self, trip1_rm_files, page_uuids, new_markdown, generator
    ):
        """Strokes that move across pages should reference the correct TreeNodeBlock."""
        new_md_doc = parse_markdown_file(new_markdown)

        rm_doc = generator.generate_document(
            md_doc=new_md_doc,
            doc_uuid="test-doc-uuid",
            parent_uuid="",
            existing_page_uuids=page_uuids,
            existing_rm_files=trip1_rm_files,
        )

        # For each page, check that all strokes reference existing TreeNodeBlocks
        orphaned_strokes = []
        for i, page in enumerate(rm_doc.pages):
            rm_bytes = generator.generate_rm_file(page)
            blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))

            # Build set of TreeNodeBlock node_ids
            tree_node_ids = set()
            for b in blocks:
                if isinstance(b, TreeNodeBlock) and b.group:
                    tree_node_ids.add((b.group.node_id.part1, b.group.node_id.part2))

            # Add system nodes
            tree_node_ids.add((0, 1))  # Root
            tree_node_ids.add((0, 11))  # Layer 1

            # Check all strokes reference existing nodes
            for b in blocks:
                if isinstance(b, SceneLineItemBlock):
                    parent = (b.parent_id.part1, b.parent_id.part2)
                    if parent not in tree_node_ids:
                        orphaned_strokes.append(
                            f"Page {i}: stroke parent_id={parent[0]}:{parent[1]} not in tree_node_ids"
                        )

        assert not orphaned_strokes, (
            "Strokes referencing non-existent TreeNodeBlocks:\n" + "\n".join(orphaned_strokes)
        )


class TestDetailedDiagnostics:
    """Detailed diagnostic tests for debugging scene graph issues."""

    def test_print_generated_scene_graph_structure(
        self, trip1_rm_files, page_uuids, new_markdown, generator, capsys
    ):
        """Print detailed scene graph structure for debugging."""
        new_md_doc = parse_markdown_file(new_markdown)

        rm_doc = generator.generate_document(
            md_doc=new_md_doc,
            doc_uuid="test-doc-uuid",
            parent_uuid="",
            existing_page_uuids=page_uuids,
            existing_rm_files=trip1_rm_files,
        )

        print("\n" + "=" * 60)
        print("GENERATED SCENE GRAPH STRUCTURE")
        print("=" * 60)

        existing_uuids = set(page_uuids)

        for i, page in enumerate(rm_doc.pages):
            rm_bytes = generator.generate_rm_file(page)
            counts = count_blocks(rm_bytes)

            generation_type = "ROUNDTRIP" if page.uuid in existing_uuids else "FROM-SCRATCH"

            print(f"\nPage {i} ({page.uuid[:12]}...) [{generation_type}]")
            print(f"  Strokes: {counts['strokes']}, Highlights: {counts['highlights']}")
            print(f"  TreeNodes: {counts['tree_nodes']}, SceneTrees: {counts['scene_trees']}")
            print(f"  SceneGroupItems: {counts['scene_group_items']}")

            # Print user TreeNodes
            user_tns = get_user_tree_nodes(rm_bytes)
            if user_tns:
                print(f"\n  User TreeNodes ({len(user_tns)}):")
                for tn in user_tns:
                    node_id = f"{tn.group.node_id.part1}:{tn.group.node_id.part2}"
                    anchor = tn.group.anchor_id.value.part2 if tn.group.anchor_id else None
                    print(f"    {node_id}: anchor={anchor}")

            # Print user SceneTrees
            user_sts = get_user_scene_trees(rm_bytes)
            if user_sts:
                print(f"\n  User SceneTrees ({len(user_sts)}):")
                for st in user_sts:
                    tree_id = f"{st.tree_id.part1}:{st.tree_id.part2}"
                    parent_id = f"{st.parent_id.part1}:{st.parent_id.part2}"
                    print(f"    tree_id={tree_id}, parent={parent_id}")

            # Print user SceneGroupItems
            user_sgis = get_user_scene_group_items(rm_bytes)
            if user_sgis:
                print(f"\n  User SceneGroupItems ({len(user_sgis)}):")
                for sgi in user_sgis:
                    value = f"{sgi.item.value.part1}:{sgi.item.value.part2}"
                    parent = f"{sgi.parent_id.part1}:{sgi.parent_id.part2}"
                    print(f"    value={value}, parent={parent}")

            # Validation
            result = validate_scene_graph(rm_bytes)
            if result.errors:
                print("\n  ERRORS:")
                for e in result.errors:
                    print(f"    {e}")
            if result.warnings:
                print("\n  WARNINGS:")
                for w in result.warnings:
                    print(f"    {w}")

        # This test always passes - it's for diagnostic output
        # Run with: pytest -v -s tests/annotations/test_cross_page_scene_graph.py::TestDetailedDiagnostics
