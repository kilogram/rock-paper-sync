"""Tests for CrdtService.

Tests the CRDT ID generation and block cloning functionality.
"""

from rmscene import CrdtId, LwwValue, TreeNodeBlock
from rmscene.scene_items import Group

from rock_paper_sync.annotations.services.crdt_service import CrdtService


class TestCrdtIdGeneration:
    """Tests for CRDT ID generation."""

    def test_generate_sequential_ids(self):
        """Test that IDs are generated sequentially."""
        service = CrdtService(base_id=100)

        id1 = service.generate_id()
        id2 = service.generate_id()
        id3 = service.generate_id()

        assert id1.part2 == 100
        assert id2.part2 == 101
        assert id3.part2 == 102

    def test_generate_with_user_author(self):
        """Test that generated IDs use user author ID (2)."""
        service = CrdtService()

        crdt_id = service.generate_id()

        assert crdt_id.part1 == 2  # User author ID

    def test_custom_base_id(self):
        """Test generation with custom base ID."""
        service = CrdtService(base_id=500)

        crdt_id = service.generate_id()

        assert crdt_id.part2 == 500

    def test_reset_counter(self):
        """Test resetting the ID counter."""
        service = CrdtService(base_id=100)
        service.generate_id()
        service.generate_id()

        service.reset()

        crdt_id = service.generate_id()
        assert crdt_id.part2 == 100

    def test_reset_with_new_base(self):
        """Test resetting with a new base ID."""
        service = CrdtService(base_id=100)
        service.generate_id()

        service.reset(base_id=200)

        crdt_id = service.generate_id()
        assert crdt_id.part2 == 200


class TestBlockCloning:
    """Tests for block cloning operations."""

    def _make_tree_node(self, anchor_offset: int) -> TreeNodeBlock:
        """Helper to create a TreeNodeBlock with given anchor."""
        return TreeNodeBlock(
            group=Group(
                node_id=CrdtId(2, 1),
                label=LwwValue(timestamp=1, value=""),
                visible=LwwValue(timestamp=1, value=True),
                anchor_id=LwwValue(timestamp=1, value=CrdtId(1, anchor_offset)),
                anchor_type=LwwValue(timestamp=1, value=0),
                anchor_threshold=LwwValue(timestamp=1, value=0.0),
                anchor_origin_x=LwwValue(timestamp=1, value=0.0),
            )
        )

    def test_clone_tree_node_with_anchor(self):
        """Test cloning TreeNodeBlock with new anchor."""
        service = CrdtService()
        old_node = self._make_tree_node(anchor_offset=42)

        new_node = service.clone_tree_node_with_anchor(old_node, new_anchor_offset=100)

        # Check new anchor
        new_anchor = new_node.group.anchor_id.value
        assert new_anchor.part2 == 100

        # Check old node unchanged
        old_anchor = old_node.group.anchor_id.value
        assert old_anchor.part2 == 42

    def test_clone_preserves_other_fields(self):
        """Test that cloning preserves other Group fields."""
        service = CrdtService()
        old_node = self._make_tree_node(anchor_offset=42)

        new_node = service.clone_tree_node_with_anchor(old_node, new_anchor_offset=100)

        # Check preserved fields
        assert new_node.group.node_id == old_node.group.node_id
        assert new_node.group.visible.value == old_node.group.visible.value

    def test_create_scene_tree_block(self):
        """Test creating a new SceneTreeBlock."""
        service = CrdtService()
        node_id = CrdtId(2, 42)

        block = service.create_scene_tree_block(node_id)

        assert block.tree_id == node_id
        assert block.parent_id == CrdtId(0, 11)  # Default layer
        assert block.is_update is True

    def test_create_scene_tree_block_custom_parent(self):
        """Test creating SceneTreeBlock with custom parent."""
        service = CrdtService()
        node_id = CrdtId(2, 42)
        parent = CrdtId(0, 99)

        block = service.create_scene_tree_block(node_id, parent_id=parent)

        assert block.parent_id == parent

    def test_create_scene_group_item_block(self):
        """Test creating a new SceneGroupItemBlock."""
        service = CrdtService()
        item_id = CrdtId(2, 1)
        value = CrdtId(2, 42)

        block = service.create_scene_group_item_block(item_id, value)

        assert block.parent_id == CrdtId(0, 11)  # Default layer
        assert block.item.item_id == item_id
        assert block.item.value == value
        assert block.item.left_id == CrdtId(0, 0)
        assert block.item.right_id == CrdtId(0, 0)


class TestBundleOperations:
    """Tests for StrokeBundle operations."""

    def _make_mock_bundle(self):
        """Create a mock StrokeBundle for testing."""
        from rock_paper_sync.annotations.scene_adapter.bundle import StrokeBundle

        return StrokeBundle(
            node_id=CrdtId(2, 42),
            tree_node=TreeNodeBlock(
                group=Group(
                    node_id=CrdtId(2, 42),
                    label=LwwValue(timestamp=1, value=""),
                    visible=LwwValue(timestamp=1, value=True),
                    anchor_id=LwwValue(timestamp=1, value=CrdtId(1, 100)),
                    anchor_type=LwwValue(timestamp=1, value=0),
                    anchor_threshold=LwwValue(timestamp=1, value=0.0),
                    anchor_origin_x=LwwValue(timestamp=1, value=0.0),
                )
            ),
            scene_tree=None,
            scene_group_item=None,
            strokes=[],
        )

    def test_reanchor_bundle(self):
        """Test reanchoring a bundle to new offset."""
        service = CrdtService()
        bundle = self._make_mock_bundle()

        new_bundle = service.reanchor_bundle(bundle, new_anchor_offset=200)

        # Check new anchor
        new_anchor = new_bundle.tree_node.group.anchor_id.value
        assert new_anchor.part2 == 200

        # Check original unchanged
        old_anchor = bundle.tree_node.group.anchor_id.value
        assert old_anchor.part2 == 100

    def test_prepare_bundle_for_page(self):
        """Test preparing bundle for page injection."""
        service = CrdtService()
        bundle = self._make_mock_bundle()

        new_bundle = service.prepare_bundle_for_page(bundle)

        # Check new scene tree was created
        assert new_bundle.scene_tree is not None
        assert new_bundle.scene_tree.tree_id == bundle.node_id

        # Check tree node preserved
        assert new_bundle.tree_node == bundle.tree_node
