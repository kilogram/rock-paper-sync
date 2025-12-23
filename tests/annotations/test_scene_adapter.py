"""Tests for the scene_adapter package.

Tests the new layered architecture components:
- BlockKind classification
- StrokeBundle extraction
- SceneTranslator operations
- PageTransformExecutor (single code path)
"""

from rock_paper_sync.annotations.domain import (
    HighlightPlacement,
    PageTransformPlan,
    PreserveUnknown,
    StrokePlacement,
)
from rock_paper_sync.annotations.scene_adapter import (
    ANNOTATION_BLOCKS,
    END_OF_DOC_ANCHOR_MARKER,
    REGENERATED_BLOCKS,
    SCENE_GRAPH_BLOCKS,
    BlockKind,
    SceneTranslator,
    classify_block,
    get_anchor_offset_from_tree_node,
    is_known_block,
    is_sentinel_anchor,
)


class TestBlockRegistry:
    """Tests for block classification."""

    def test_classify_known_blocks(self):
        """Known block types are classified correctly."""

        # Create mock blocks for testing classification
        # Note: We're testing type name matching, not full block construction

        class MockAuthorIdsBlock:
            pass

        MockAuthorIdsBlock.__name__ = "AuthorIdsBlock"

        class MockMigrationInfoBlock:
            pass

        MockMigrationInfoBlock.__name__ = "MigrationInfoBlock"

        class MockPageInfoBlock:
            pass

        MockPageInfoBlock.__name__ = "PageInfoBlock"

        class MockRootTextBlock:
            pass

        MockRootTextBlock.__name__ = "RootTextBlock"

        class MockSceneTreeBlock:
            pass

        MockSceneTreeBlock.__name__ = "SceneTreeBlock"

        class MockTreeNodeBlock:
            pass

        MockTreeNodeBlock.__name__ = "TreeNodeBlock"

        class MockSceneGroupItemBlock:
            pass

        MockSceneGroupItemBlock.__name__ = "SceneGroupItemBlock"

        class MockSceneLineItemBlock:
            pass

        MockSceneLineItemBlock.__name__ = "SceneLineItemBlock"

        class MockSceneGlyphItemBlock:
            pass

        MockSceneGlyphItemBlock.__name__ = "SceneGlyphItemBlock"

        # Test classifications
        assert classify_block(MockAuthorIdsBlock()) == BlockKind.AUTHOR_IDS
        assert classify_block(MockMigrationInfoBlock()) == BlockKind.MIGRATION_INFO
        assert classify_block(MockPageInfoBlock()) == BlockKind.PAGE_INFO
        assert classify_block(MockRootTextBlock()) == BlockKind.ROOT_TEXT
        assert classify_block(MockSceneTreeBlock()) == BlockKind.SCENE_TREE
        assert classify_block(MockTreeNodeBlock()) == BlockKind.TREE_NODE
        assert classify_block(MockSceneGroupItemBlock()) == BlockKind.SCENE_GROUP_ITEM
        assert classify_block(MockSceneLineItemBlock()) == BlockKind.STROKE
        assert classify_block(MockSceneGlyphItemBlock()) == BlockKind.HIGHLIGHT

    def test_classify_unknown_blocks(self):
        """Unknown block types are classified as UNKNOWN."""

        class SomeFutureBlockType:
            pass

        assert classify_block(SomeFutureBlockType()) == BlockKind.UNKNOWN

    def test_is_known_block(self):
        """is_known_block returns correct boolean."""

        class MockSceneTreeBlock:
            pass

        MockSceneTreeBlock.__name__ = "SceneTreeBlock"

        class MockFutureBlock:
            pass

        assert is_known_block(MockSceneTreeBlock()) is True
        assert is_known_block(MockFutureBlock()) is False

    def test_block_categories_are_disjoint(self):
        """Block categories don't overlap."""
        all_categories = [REGENERATED_BLOCKS, ANNOTATION_BLOCKS, SCENE_GRAPH_BLOCKS]

        for i, cat1 in enumerate(all_categories):
            for cat2 in all_categories[i + 1 :]:
                intersection = cat1 & cat2
                assert not intersection, f"Categories overlap: {intersection}"


class TestDomainIntents:
    """Tests for domain intent types."""

    def test_stroke_placement_creation(self):
        """StrokePlacement can be created with required fields."""
        placement = StrokePlacement(
            opaque_handle="mock_bundle",
            anchor_char_offset=42,
        )
        assert placement.anchor_char_offset == 42
        assert placement.opaque_handle == "mock_bundle"
        assert placement.source_page_idx is None

    def test_stroke_placement_with_all_fields(self):
        """StrokePlacement can be created with all optional fields."""
        placement = StrokePlacement(
            opaque_handle="mock_bundle",
            anchor_char_offset=42,
            source_page_idx=1,
            relative_y_offset=10.5,
        )
        assert placement.source_page_idx == 1
        assert placement.relative_y_offset == 10.5

    def test_highlight_placement_creation(self):
        """HighlightPlacement can be created."""
        placement = HighlightPlacement(
            opaque_handle="mock_highlight",
            start_offset=10,
            end_offset=20,
        )
        assert placement.start_offset == 10
        assert placement.end_offset == 20

    def test_preserve_unknown_creation(self):
        """PreserveUnknown can be created."""
        unknown = PreserveUnknown(opaque_handle="unknown_block")
        assert unknown.opaque_handle == "unknown_block"

    def test_page_transform_plan_creation(self):
        """PageTransformPlan can be created with defaults."""
        plan = PageTransformPlan(
            page_uuid="test-uuid",
            page_text="Hello world",
        )
        assert plan.page_uuid == "test-uuid"
        assert plan.page_text == "Hello world"
        assert plan.stroke_placements == []
        assert plan.highlight_placements == []
        assert plan.unknown_blocks == []
        assert plan.source_rm_path is None

    def test_page_transform_plan_has_annotations(self):
        """has_annotations property works correctly."""
        empty_plan = PageTransformPlan(page_uuid="1", page_text="")
        assert not empty_plan.has_annotations

        stroke_plan = PageTransformPlan(
            page_uuid="2",
            page_text="",
            stroke_placements=[StrokePlacement(opaque_handle="b", anchor_char_offset=0)],
        )
        assert stroke_plan.has_annotations

    def test_page_transform_plan_is_roundtrip(self):
        """is_roundtrip property works correctly."""
        from pathlib import Path

        no_source = PageTransformPlan(page_uuid="1", page_text="")
        assert not no_source.is_roundtrip

        nonexistent = PageTransformPlan(
            page_uuid="2",
            page_text="",
            source_rm_path=Path("/nonexistent/path.rm"),
        )
        assert not nonexistent.is_roundtrip


class TestSceneTranslator:
    """Tests for SceneTranslator operations."""

    def test_translator_instantiation(self):
        """SceneTranslator can be instantiated."""
        translator = SceneTranslator()
        assert translator is not None

    def test_get_anchor_offset_from_none(self):
        """get_anchor_offset_from_tree_node handles None."""
        assert get_anchor_offset_from_tree_node(None) is None

    def test_is_sentinel_anchor_with_none(self):
        """is_sentinel_anchor handles None."""
        assert is_sentinel_anchor(None) is False

    def test_end_of_doc_marker_value(self):
        """END_OF_DOC_ANCHOR_MARKER has expected value."""
        # This is the sentinel value used in rmscene
        assert END_OF_DOC_ANCHOR_MARKER == 281474976710655


class TestPageTransformExecutor:
    """Tests for PageTransformExecutor."""

    def test_executor_generates_valid_rm_file(self):
        """Executor generates a valid .rm file from a simple plan."""
        from rock_paper_sync.annotations.scene_adapter import (
            PageTransformExecutor,
            validate_scene_graph,
        )
        from rock_paper_sync.layout.device import DEFAULT_DEVICE

        executor = PageTransformExecutor(DEFAULT_DEVICE)
        plan = PageTransformPlan(
            page_uuid="test-uuid",
            page_text="Hello, world!\nThis is a test.",
        )

        rm_bytes = executor.execute(plan)

        # Verify we got bytes back
        assert isinstance(rm_bytes, bytes)
        assert len(rm_bytes) > 0

        # Verify the scene graph is valid
        validation = validate_scene_graph(rm_bytes)
        assert validation.is_valid, f"Validation errors: {validation.errors}"

    def test_executor_generates_proper_text_styles(self):
        """Executor generates proper newline styles."""
        import io

        import rmscene

        from rock_paper_sync.annotations.scene_adapter import PageTransformExecutor
        from rock_paper_sync.layout.device import DEFAULT_DEVICE

        executor = PageTransformExecutor(DEFAULT_DEVICE)
        plan = PageTransformPlan(
            page_uuid="test-uuid",
            page_text="Line 1\nLine 2\nLine 3",
        )

        rm_bytes = executor.execute(plan)

        # Parse the generated file and check for RootTextBlock
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        root_text_blocks = [b for b in blocks if type(b).__name__ == "RootTextBlock"]

        assert len(root_text_blocks) == 1
        root_text = root_text_blocks[0]

        # Check that text content is correct
        text_items = list(root_text.value.items.sequence_items())
        assert len(text_items) == 1
        assert text_items[0].value == "Line 1\nLine 2\nLine 3"

    def test_executor_generates_system_nodes(self):
        """Executor generates required system TreeNodeBlocks."""
        import io

        import rmscene
        from rmscene import CrdtId

        from rock_paper_sync.annotations.scene_adapter import PageTransformExecutor
        from rock_paper_sync.layout.device import DEFAULT_DEVICE

        executor = PageTransformExecutor(DEFAULT_DEVICE)
        plan = PageTransformPlan(
            page_uuid="test-uuid",
            page_text="Test content",
        )

        rm_bytes = executor.execute(plan)
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))

        # Check for system TreeNodeBlocks
        tree_nodes = [b for b in blocks if type(b).__name__ == "TreeNodeBlock"]
        node_ids = {b.group.node_id for b in tree_nodes}

        # Must have root (0:1) and layer 1 (0:11)
        assert CrdtId(0, 1) in node_ids, "Missing root TreeNodeBlock (0:1)"
        assert CrdtId(0, 11) in node_ids, "Missing Layer 1 TreeNodeBlock (0:11)"


class TestBackwardCompatibility:
    """Tests that old imports still work."""

    def test_scene_graph_imports(self):
        """Old scene_graph imports still work."""
        from rock_paper_sync.annotations.scene_graph import (
            SceneGraphIndex,
            StrokeBundle,
        )

        # Just verify imports work
        assert StrokeBundle is not None
        assert SceneGraphIndex is not None

    def test_scene_adapter_exports_same_types(self):
        """scene_adapter exports the same types as old scene_graph."""
        from rock_paper_sync.annotations import scene_adapter, scene_graph

        # Core types should be the same
        assert scene_graph.StrokeBundle is scene_adapter.StrokeBundle
        assert scene_graph.SceneGraphIndex is scene_adapter.SceneGraphIndex
        assert scene_graph.validate_scene_graph is scene_adapter.validate_scene_graph
