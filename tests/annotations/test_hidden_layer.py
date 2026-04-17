"""Tests for M5.5 hidden layer preservation (HiddenLayerManager, executor multi-layer output)."""

import io

import rmscene
from rmscene import CrdtId

from rock_paper_sync.annotations.domain import (
    LayerPlan,
    LayerType,
    PageTransformPlan,
    PreserveUnknown,
)
from rock_paper_sync.annotations.scene_adapter import PageTransformExecutor
from rock_paper_sync.annotations.scene_adapter.scene_index import (
    SYSTEM_LAYER_1,
    SYSTEM_LAYER_2,
    layer_crdt_id,
)
from rock_paper_sync.annotations.services.hidden_layer import (
    CONTENT_LAYER_ID,
    PRESERVATION_LAYER_ID,
    PRESERVATION_LAYER_LABEL,
    HiddenLayerManager,
    deserialize_annotation_blocks,
    reparent_blocks_to_preservation,
    serialize_annotation_blocks,
)
from rock_paper_sync.layout.device import DEFAULT_DEVICE

# ===========================================================================
# Fixtures
# ===========================================================================


def _make_minimal_rm_bytes() -> bytes:
    """Create minimal valid .rm bytes with one SceneGlyphItemBlock (highlight)."""

    # We don't actually need a real highlight block — just a PreserveUnknown
    # wrapping any small valid block. Use the blocks from a generated .rm.
    executor = PageTransformExecutor(DEFAULT_DEVICE)
    plan = PageTransformPlan(page_uuid="test-uuid", page_text="hello")
    return executor.execute(plan)


def _minimal_preservation_blob() -> bytes:
    """Return a small valid blob (subset of blocks from a real .rm file)."""
    rm_bytes = _make_minimal_rm_bytes()
    # Use only the first few blocks to keep the blob small
    all_blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    # Take just the AuthorIdsBlock and MigrationInfoBlock (first 2)
    subset = all_blocks[:2]
    buf = io.BytesIO()
    rmscene.write_blocks(buf, subset)
    return buf.getvalue()


# ===========================================================================
# layer_crdt_id tests
# ===========================================================================


class TestLayerCrdtId:
    def test_layer_0_is_system_layer_1(self):
        assert layer_crdt_id(0) == SYSTEM_LAYER_1  # CrdtId(0, 11)

    def test_layer_1_is_system_layer_2(self):
        assert layer_crdt_id(1) == SYSTEM_LAYER_2  # CrdtId(0, 21)

    def test_layer_2_id(self):
        assert layer_crdt_id(2) == CrdtId(0, 31)

    def test_layer_ids_are_distinct(self):
        ids = [layer_crdt_id(i) for i in range(5)]
        assert len(set(ids)) == 5


# ===========================================================================
# Serialization / deserialization round-trip
# ===========================================================================


class TestSerializeDeserialize:
    def test_deserialize_roundtrip(self):
        blob = _minimal_preservation_blob()
        blocks = deserialize_annotation_blocks(blob)
        assert len(blocks) >= 1

    def test_serialize_annotation_blocks_returns_none_without_rm_block(self):
        """If annotation has no original_rm_block, serialize returns None."""

        class FakeAnnotation:
            annotation_id = "fake-id"
            annotation_type = "highlight"
            original_rm_block = None

        result = serialize_annotation_blocks(FakeAnnotation())
        assert result is None

    def test_serialize_annotation_blocks_for_stroke_without_any_blocks(self):
        class FakeStroke:
            annotation_id = "fake-id"
            annotation_type = "stroke"
            original_scene_tree_block = None
            original_tree_node = None
            original_scene_group_item = None
            original_rm_block = None

        result = serialize_annotation_blocks(FakeStroke())
        assert result is None


# ===========================================================================
# Re-parenting
# ===========================================================================


class TestReparentBlocks:
    def test_blocks_with_content_layer_parent_get_reparented(self):
        from dataclasses import dataclass

        @dataclass
        class FakeBlock:
            parent_id: CrdtId

        block = FakeBlock(parent_id=CONTENT_LAYER_ID)
        result = reparent_blocks_to_preservation([block])
        assert result[0].parent_id == PRESERVATION_LAYER_ID

    def test_blocks_with_other_parent_are_unchanged(self):
        from dataclasses import dataclass

        other_id = CrdtId(0, 999)

        @dataclass
        class FakeBlock:
            parent_id: CrdtId

        block = FakeBlock(parent_id=other_id)
        result = reparent_blocks_to_preservation([block])
        assert result[0].parent_id == other_id

    def test_blocks_without_parent_id_are_unchanged(self):
        class FakeBlock:
            value = "no parent"

        block = FakeBlock()
        result = reparent_blocks_to_preservation([block])
        assert result[0] is block

    def test_reparenting_does_not_mutate_original(self):
        from dataclasses import dataclass

        @dataclass
        class FakeBlock:
            parent_id: CrdtId

        block = FakeBlock(parent_id=CONTENT_LAYER_ID)
        result = reparent_blocks_to_preservation([block])
        assert block.parent_id == CONTENT_LAYER_ID  # original unchanged
        assert result[0] is not block


# ===========================================================================
# HiddenLayerManager
# ===========================================================================


class TestHiddenLayerManager:
    def test_returns_none_for_empty_blobs(self):
        manager = HiddenLayerManager()
        assert manager.build_preservation_layer([]) is None

    def test_returns_none_for_invalid_blobs(self):
        manager = HiddenLayerManager()
        result = manager.build_preservation_layer([b"not valid rmscene data"])
        assert result is None

    def test_returns_layer_plan_for_valid_blobs(self):
        blob = _minimal_preservation_blob()
        manager = HiddenLayerManager()
        layer = manager.build_preservation_layer([blob])
        assert layer is not None
        assert layer.layer_type == LayerType.PRESERVATION
        assert layer.visible is False
        assert layer.label == PRESERVATION_LAYER_LABEL
        assert len(layer.unknown_blocks) >= 1

    def test_unknown_blocks_are_preserve_unknown(self):
        blob = _minimal_preservation_blob()
        manager = HiddenLayerManager()
        layer = manager.build_preservation_layer([blob])
        assert layer is not None
        for item in layer.unknown_blocks:
            assert isinstance(item, PreserveUnknown)

    def test_multiple_blobs_combine_into_one_layer(self):
        blob = _minimal_preservation_blob()
        manager = HiddenLayerManager()
        layer_single = manager.build_preservation_layer([blob])
        layer_double = manager.build_preservation_layer([blob, blob])
        assert layer_single is not None
        assert layer_double is not None
        assert len(layer_double.unknown_blocks) == 2 * len(layer_single.unknown_blocks)

    def test_skips_invalid_blob_and_processes_valid(self):
        blob = _minimal_preservation_blob()
        manager = HiddenLayerManager()
        layer = manager.build_preservation_layer([b"garbage", blob])
        assert layer is not None
        assert len(layer.unknown_blocks) >= 1


# ===========================================================================
# Executor multi-layer output
# ===========================================================================


class TestExecutorMultiLayer:
    def _execute_two_layer_plan(self) -> bytes:
        blob = _minimal_preservation_blob()
        manager = HiddenLayerManager()
        preservation_layer = manager.build_preservation_layer([blob])
        assert preservation_layer is not None

        content_layer = LayerPlan(
            layer_type=LayerType.CONTENT,
            visible=True,
            label="Layer 1",
        )
        plan = PageTransformPlan(
            page_uuid="multi-layer-uuid",
            page_text="Hello multi-layer",
            layers=[content_layer, preservation_layer],
        )
        executor = PageTransformExecutor(DEFAULT_DEVICE)
        return executor.execute(plan)

    def test_two_layers_produce_two_scene_tree_blocks(self):
        rm_bytes = self._execute_two_layer_plan()
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        scene_tree_blocks = [b for b in blocks if type(b).__name__ == "SceneTreeBlock"]
        # One SceneTreeBlock per layer (plus root counts as TreeNodeBlock, not SceneTreeBlock)
        assert len(scene_tree_blocks) >= 2

    def test_two_layers_produce_two_layer_tree_nodes(self):
        rm_bytes = self._execute_two_layer_plan()
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        tree_nodes = [b for b in blocks if type(b).__name__ == "TreeNodeBlock"]
        node_ids = {b.group.node_id for b in tree_nodes}
        # Both layer ids must be present
        assert SYSTEM_LAYER_1 in node_ids, f"Layer 1 (0:11) missing from {node_ids}"
        assert SYSTEM_LAYER_2 in node_ids, f"Layer 2 (0:21) missing from {node_ids}"

    def test_preservation_layer_tree_node_is_invisible(self):
        rm_bytes = self._execute_two_layer_plan()
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        tree_nodes = [b for b in blocks if type(b).__name__ == "TreeNodeBlock"]
        preservation_nodes = [b for b in tree_nodes if b.group.node_id == SYSTEM_LAYER_2]
        assert preservation_nodes, "No TreeNodeBlock for preservation layer found"
        node = preservation_nodes[0]
        assert node.group.visible.value is False

    def test_content_layer_tree_node_is_visible(self):
        rm_bytes = self._execute_two_layer_plan()
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        tree_nodes = [b for b in blocks if type(b).__name__ == "TreeNodeBlock"]
        content_nodes = [b for b in tree_nodes if b.group.node_id == SYSTEM_LAYER_1]
        assert content_nodes, "No TreeNodeBlock for content layer found"
        node = content_nodes[0]
        # Default visible is True (LwwValue default or explicit)
        assert node.group.visible.value is True

    def test_single_layer_plan_still_works(self):
        """Backward compatibility: single-layer plan generates valid .rm."""
        from rock_paper_sync.annotations.scene_adapter import validate_scene_graph

        content_layer = LayerPlan(
            layer_type=LayerType.CONTENT,
            visible=True,
            label="Layer 1",
        )
        plan = PageTransformPlan(
            page_uuid="single-layer-uuid",
            page_text="Single layer test",
            layers=[content_layer],
        )
        executor = PageTransformExecutor(DEFAULT_DEVICE)
        rm_bytes = executor.execute(plan)
        validation = validate_scene_graph(rm_bytes)
        assert validation.is_valid, f"Validation errors: {validation.errors}"

    def test_empty_layers_plan_still_works(self):
        """Backward compatibility: plan with empty layers list generates valid .rm."""
        from rock_paper_sync.annotations.scene_adapter import validate_scene_graph

        plan = PageTransformPlan(
            page_uuid="empty-layers-uuid",
            page_text="Empty layers test",
        )
        executor = PageTransformExecutor(DEFAULT_DEVICE)
        rm_bytes = executor.execute(plan)
        validation = validate_scene_graph(rm_bytes)
        assert validation.is_valid, f"Validation errors: {validation.errors}"
