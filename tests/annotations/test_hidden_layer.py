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


# ===========================================================================
# State DB: blocks_blob roundtrip
# ===========================================================================


class TestOrphanedAnnotationBlobRoundtrip:
    """State-layer tests: blocks_blob and source_page_idx survive DB storage."""

    def _make_state(self, tmp_path):
        from rock_paper_sync.state import StateManager

        return StateManager(tmp_path / "state.db")

    def test_blocks_blob_stored_and_retrieved(self, tmp_path):
        from rock_paper_sync.state import OrphanedAnnotation

        state = self._make_state(tmp_path)
        blob = _minimal_preservation_blob()
        orphan = OrphanedAnnotation(
            vault_name="v",
            obsidian_path="a.md",
            annotation_id="ann-1",
            annotation_type="highlight",
            original_anchor_text="hello",
            orphaned_at=1000,
            blocks_blob=blob,
            source_page_idx=2,
        )
        state.add_orphaned_annotation(orphan)

        rows = state.get_orphaned_annotations("v", "a.md")
        assert len(rows) == 1
        assert rows[0].blocks_blob == blob
        assert rows[0].source_page_idx == 2

    def test_blocks_blob_none_is_stored_as_null(self, tmp_path):
        from rock_paper_sync.state import OrphanedAnnotation

        state = self._make_state(tmp_path)
        orphan = OrphanedAnnotation(
            vault_name="v",
            obsidian_path="a.md",
            annotation_id="ann-null",
            annotation_type="highlight",
            original_anchor_text="world",
            orphaned_at=1000,
            blocks_blob=None,
            source_page_idx=None,
        )
        state.add_orphaned_annotation(orphan)

        rows = state.get_orphaned_annotations("v", "a.md")
        assert rows[0].blocks_blob is None
        assert rows[0].source_page_idx is None

    def test_get_orphan_blobs_excludes_null_blob_rows(self, tmp_path):
        from rock_paper_sync.state import OrphanedAnnotation

        state = self._make_state(tmp_path)
        blob = _minimal_preservation_blob()

        state.add_orphaned_annotation(
            OrphanedAnnotation(
                vault_name="v",
                obsidian_path="a.md",
                annotation_id="ann-with-blob",
                annotation_type="highlight",
                original_anchor_text=None,
                orphaned_at=1000,
                blocks_blob=blob,
                source_page_idx=0,
            )
        )
        state.add_orphaned_annotation(
            OrphanedAnnotation(
                vault_name="v",
                obsidian_path="a.md",
                annotation_id="ann-no-blob",
                annotation_type="stroke",
                original_anchor_text=None,
                orphaned_at=1000,
                blocks_blob=None,
                source_page_idx=None,
            )
        )

        blobs = state.get_orphan_blobs_for_document("v", "a.md")
        assert len(blobs) == 1
        ann_id, page_idx, returned_blob = blobs[0]
        assert ann_id == "ann-with-blob"
        assert page_idx == 0
        assert returned_blob == blob

    def test_get_orphan_blobs_returns_empty_when_none_stored(self, tmp_path):
        state = self._make_state(tmp_path)
        assert state.get_orphan_blobs_for_document("v", "a.md") == []

    def test_get_orphan_blobs_is_vault_scoped(self, tmp_path):
        from rock_paper_sync.state import OrphanedAnnotation

        state = self._make_state(tmp_path)
        blob = _minimal_preservation_blob()

        state.add_orphaned_annotation(
            OrphanedAnnotation(
                vault_name="vault-A",
                obsidian_path="a.md",
                annotation_id="ann-1",
                annotation_type="highlight",
                original_anchor_text=None,
                orphaned_at=1000,
                blocks_blob=blob,
                source_page_idx=0,
            )
        )

        assert state.get_orphan_blobs_for_document("vault-B", "a.md") == []
        assert len(state.get_orphan_blobs_for_document("vault-A", "a.md")) == 1

    def test_blocks_blob_replaced_on_upsert(self, tmp_path):
        """INSERT OR REPLACE updates the blob when the same annotation is re-recorded."""
        from rock_paper_sync.state import OrphanedAnnotation

        state = self._make_state(tmp_path)
        blob1 = _minimal_preservation_blob()
        blob2 = _minimal_preservation_blob() + b"\x00"

        base = dict(
            vault_name="v",
            obsidian_path="a.md",
            annotation_id="ann-1",
            annotation_type="highlight",
            original_anchor_text=None,
            orphaned_at=1000,
        )
        state.add_orphaned_annotation(OrphanedAnnotation(**base, blocks_blob=blob1))
        state.add_orphaned_annotation(OrphanedAnnotation(**base, blocks_blob=blob2))

        rows = state.get_orphaned_annotations("v", "a.md")
        assert len(rows) == 1
        assert rows[0].blocks_blob == blob2


# ===========================================================================
# _record_orphans: real DocumentAnnotation with rmscene block
# ===========================================================================


class TestRecordOrphansWithBlob:
    """Tests that _record_orphans correctly serializes real rmscene blocks."""

    def _make_engine(self, tmp_path):
        from unittest.mock import MagicMock

        from rock_paper_sync.pull_sync import PullSyncEngine
        from rock_paper_sync.state import StateManager

        state = StateManager(tmp_path / "state.db")
        return (
            PullSyncEngine(
                state=state,
                cloud_sync=MagicMock(),
                annotation_helper=MagicMock(),
                cache_dir=tmp_path / "cache",
            ),
            state,
        )

    def _make_change(self):
        from rock_paper_sync.annotation_sync_helper import AnnotationChange

        return AnnotationChange(
            vault_name="v",
            obsidian_path="a.md",
            remarkable_uuid="uuid-123",
            change_type="modified",
            current_annotation_hash="hash-abc",
            previous_annotation_hash=None,
        )

    def test_orphan_without_rm_block_stores_null_blob(self, tmp_path):
        from rock_paper_sync.annotations.document_model import AnchorContext, DocumentAnnotation

        engine, state = self._make_engine(tmp_path)
        content = "hello world"
        anchor = AnchorContext.from_text_span(content, 0, 5)
        orphan = DocumentAnnotation(
            annotation_id="ann-no-block",
            annotation_type="highlight",
            source_page_idx=1,
            anchor_context=anchor,
            original_rm_block=None,
        )

        engine._record_orphans(self._make_change(), [orphan])

        assert state.get_orphan_blobs_for_document("v", "a.md") == []
        rows = state.get_orphaned_annotations("v", "a.md")
        assert len(rows) == 1
        assert rows[0].blocks_blob is None
        assert rows[0].source_page_idx == 1

    def test_orphan_source_page_idx_is_stored(self, tmp_path):
        from rock_paper_sync.annotations.document_model import AnchorContext, DocumentAnnotation

        engine, state = self._make_engine(tmp_path)
        content = "text"
        anchor = AnchorContext.from_text_span(content, 0, 4)
        orphan = DocumentAnnotation(
            annotation_id="ann-page-3",
            annotation_type="stroke",
            source_page_idx=3,
            anchor_context=anchor,
        )

        engine._record_orphans(self._make_change(), [orphan])

        rows = state.get_orphaned_annotations("v", "a.md")
        assert rows[0].source_page_idx == 3

    def test_record_multiple_orphans_all_stored(self, tmp_path):
        from rock_paper_sync.annotations.document_model import AnchorContext, DocumentAnnotation

        engine, state = self._make_engine(tmp_path)
        content = "hello world foo bar"
        orphans = [
            DocumentAnnotation(
                annotation_id=f"ann-{i}",
                annotation_type="highlight",
                source_page_idx=i,
                anchor_context=AnchorContext.from_text_span(content, 0, 5),
            )
            for i in range(3)
        ]

        engine._record_orphans(self._make_change(), orphans)

        rows = state.get_orphaned_annotations("v", "a.md")
        assert len(rows) == 3
        assert {r.annotation_id for r in rows} == {"ann-0", "ann-1", "ann-2"}

    def test_re_recording_clears_previous_orphans(self, tmp_path):
        from rock_paper_sync.annotations.document_model import AnchorContext, DocumentAnnotation

        engine, state = self._make_engine(tmp_path)
        content = "hello"
        anchor = AnchorContext.from_text_span(content, 0, 5)
        change = self._make_change()

        engine._record_orphans(
            change,
            [
                DocumentAnnotation(
                    annotation_id="old",
                    annotation_type="highlight",
                    source_page_idx=0,
                    anchor_context=anchor,
                )
            ],
        )
        engine._record_orphans(
            change,
            [
                DocumentAnnotation(
                    annotation_id="new",
                    annotation_type="highlight",
                    source_page_idx=0,
                    anchor_context=anchor,
                )
            ],
        )

        rows = state.get_orphaned_annotations("v", "a.md")
        assert len(rows) == 1
        assert rows[0].annotation_id == "new"


# ===========================================================================
# End-to-end: state blobs → generator → .rm with hidden layer
# ===========================================================================


class TestGeneratorOrphanBlobsIntegration:
    """Tests the full pipeline: orphan blobs → generate_document → .rm bytes."""

    def _make_generator(self):
        from rock_paper_sync.config import LayoutConfig
        from rock_paper_sync.generator import RemarkableGenerator

        return RemarkableGenerator(LayoutConfig())

    def _make_md_doc(self, title="Test", content="Hello world"):
        import tempfile
        from pathlib import Path

        from rock_paper_sync.parser import parse_markdown_file

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(f"# {title}\n\n{content}\n")
            tmp = Path(f.name)
        try:
            return parse_markdown_file(tmp)
        finally:
            tmp.unlink()

    def test_generate_document_with_orphan_blobs_sets_page0_blobs(self):
        blob = _minimal_preservation_blob()
        gen = self._make_generator()

        rm_doc = gen.generate_document(self._make_md_doc(), orphan_blobs=[blob])

        assert rm_doc.pages[0].orphan_blobs == [blob]

    def test_generate_document_without_blobs_has_empty_page_blobs(self):
        gen = self._make_generator()

        rm_doc = gen.generate_document(self._make_md_doc())

        assert rm_doc.pages[0].orphan_blobs == []

    def test_generate_rm_file_with_blobs_produces_preservation_layer(self):
        blob = _minimal_preservation_blob()
        gen = self._make_generator()
        rm_doc = gen.generate_document(self._make_md_doc(), orphan_blobs=[blob])
        rm_bytes = gen.generate_rm_file(rm_doc.pages[0])

        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        node_ids = {b.group.node_id for b in blocks if type(b).__name__ == "TreeNodeBlock"}

        assert SYSTEM_LAYER_1 in node_ids, "Content layer (0:11) missing"
        assert SYSTEM_LAYER_2 in node_ids, "Preservation layer (0:21) missing"

    def test_generate_rm_file_preservation_layer_is_invisible(self):
        blob = _minimal_preservation_blob()
        gen = self._make_generator()
        rm_doc = gen.generate_document(self._make_md_doc(), orphan_blobs=[blob])
        rm_bytes = gen.generate_rm_file(rm_doc.pages[0])

        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        tree_nodes = [b for b in blocks if type(b).__name__ == "TreeNodeBlock"]
        preservation = [b for b in tree_nodes if b.group.node_id == SYSTEM_LAYER_2]

        assert preservation, "No TreeNodeBlock for preservation layer"
        assert preservation[0].group.visible.value is False

    def test_generate_rm_file_without_blobs_has_no_preservation_layer(self):
        gen = self._make_generator()
        rm_bytes = gen.generate_rm_file(gen.generate_document(self._make_md_doc()).pages[0])

        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        node_ids = {b.group.node_id for b in blocks if type(b).__name__ == "TreeNodeBlock"}

        assert SYSTEM_LAYER_2 not in node_ids, "No preservation layer expected when no blobs"

    def test_only_first_page_gets_orphan_blobs(self):
        """Orphan blobs attach only to page 0, not subsequent pages."""
        # Build a large document via the parser to ensure multiple pages
        long_content = "\n\n".join(f"Paragraph {i}. " * 20 for i in range(50))
        md_doc = self._make_md_doc(content=long_content)

        blob = _minimal_preservation_blob()
        gen = self._make_generator()
        rm_doc = gen.generate_document(md_doc, orphan_blobs=[blob])

        assert len(rm_doc.pages) >= 2, "Expected multiple pages for this test to be meaningful"
        assert rm_doc.pages[0].orphan_blobs == [blob]
        for page in rm_doc.pages[1:]:
            assert page.orphan_blobs == []
