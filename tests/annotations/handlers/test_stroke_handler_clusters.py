"""Tests for StrokeHandler cluster-based interface.

Tests the new cluster-based methods:
- detect_clusters()
- migrate_clusters()
- serialize_for_page()
"""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

from rmscene import CrdtId, TreeNodeBlock

from rock_paper_sync.annotations.document_model import AnchorContext, PageProjection
from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler
from rock_paper_sync.annotations.services.crdt_service import CrdtService
from rock_paper_sync.annotations.stroke import Stroke
from rock_paper_sync.annotations.stroke_cluster import StrokeCluster


@dataclass
class MockBundle:
    """Mock StrokeBundle for testing."""

    node_id: CrdtId
    tree_node: TreeNodeBlock | None = None
    anchor_offset: int | None = None

    def to_raw_blocks(self) -> list:
        """Return mock blocks."""
        return [f"block_{self.node_id.part2}"]


class TestDetectClusters:
    """Tests for detect_clusters()."""

    def test_detect_clusters_from_rm_file(self, tmp_path: Path):
        """Test that detect_clusters delegates to StrokeCluster.from_rm_file."""
        handler = StrokeHandler()

        with patch(
            "rock_paper_sync.annotations.stroke_cluster.StrokeCluster.from_rm_file"
        ) as mock_from_rm:
            mock_from_rm.return_value = [
                StrokeCluster(
                    cluster_id="abc123",
                    strokes=[],
                    bounding_box=(0, 0, 100, 100),
                )
            ]

            rm_path = tmp_path / "test.rm"
            rm_path.touch()

            clusters = handler.detect_clusters(rm_path)

            assert len(clusters) == 1
            assert clusters[0].cluster_id == "abc123"
            mock_from_rm.assert_called_once_with(rm_path, 80.0)

    def test_detect_clusters_custom_threshold(self, tmp_path: Path):
        """Test detect_clusters with custom distance threshold."""
        handler = StrokeHandler()

        with patch(
            "rock_paper_sync.annotations.stroke_cluster.StrokeCluster.from_rm_file"
        ) as mock_from_rm:
            mock_from_rm.return_value = []

            rm_path = tmp_path / "test.rm"
            rm_path.touch()

            handler.detect_clusters(rm_path, distance_threshold=50.0)

            mock_from_rm.assert_called_once_with(rm_path, 50.0)


class TestMigrateClusters:
    """Tests for migrate_clusters()."""

    def _make_mock_anchor(self) -> AnchorContext:
        """Create mock AnchorContext."""
        return AnchorContext(
            content_hash="abc123",
            text_content="test content",
            paragraph_index=0,
            context_before="before",
            context_after="after",
        )

    def _make_mock_stroke(self, bundle: MockBundle) -> Stroke:
        """Create mock Stroke with bundle reference."""
        mock_line_block = MagicMock()
        return Stroke(
            stroke_id=CrdtId(2, 1),
            points=[],
            bounding_box=(0, 0, 50, 50),
            color=0,
            tool=0,
            thickness=2.0,
            tree_node_id=CrdtId(2, 1),
            line_block=mock_line_block,
            bundle=bundle,
        )

    def _make_mock_cluster(
        self,
        anchor: AnchorContext | None = None,
        bundle: MockBundle | None = None,
    ) -> StrokeCluster:
        """Create mock StrokeCluster."""
        if bundle is None:
            bundle = MockBundle(node_id=CrdtId(2, 42), anchor_offset=100)

        stroke = self._make_mock_stroke(bundle)
        cluster = StrokeCluster(
            cluster_id="test123",
            strokes=[stroke],
            bounding_box=(0, 0, 100, 100),
            anchor=anchor,
        )
        cluster._bundles = [bundle]
        return cluster

    def test_migrate_clusters_success(self):
        """Test successful cluster migration."""
        handler = StrokeHandler()

        # Create mock resolver that returns resolved anchor
        mock_resolver = MagicMock()
        mock_resolved = MagicMock()
        mock_resolved.start_offset = 200
        mock_resolved.confidence = 0.95
        mock_resolved.match_type = "exact"
        mock_resolver.resolve.return_value = mock_resolved

        # Create mock CrdtService
        mock_crdt_service = MagicMock(spec=CrdtService)
        mock_crdt_service.reanchor_bundle.side_effect = lambda b, offset: b

        anchor = self._make_mock_anchor()
        cluster = self._make_mock_cluster(anchor=anchor)

        migrated = handler.migrate_clusters(
            clusters=[cluster],
            old_text="old document text",
            new_text="new document text",
            context_resolver=mock_resolver,
            crdt_service=mock_crdt_service,
        )

        assert len(migrated) == 1
        mock_resolver.resolve.assert_called_once()
        mock_crdt_service.reanchor_bundle.assert_called()

    def test_migrate_clusters_no_anchor(self):
        """Test that clusters without anchors are skipped."""
        handler = StrokeHandler()
        mock_resolver = MagicMock()

        # Cluster with no anchor
        cluster = self._make_mock_cluster(anchor=None)

        migrated = handler.migrate_clusters(
            clusters=[cluster],
            old_text="old",
            new_text="new",
            context_resolver=mock_resolver,
        )

        assert len(migrated) == 0
        mock_resolver.resolve.assert_not_called()

    def test_migrate_clusters_resolve_failure(self):
        """Test that unresolvable clusters are dropped."""
        handler = StrokeHandler()

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = None  # Cannot resolve

        anchor = self._make_mock_anchor()
        cluster = self._make_mock_cluster(anchor=anchor)

        migrated = handler.migrate_clusters(
            clusters=[cluster],
            old_text="old",
            new_text="new",
            context_resolver=mock_resolver,
        )

        assert len(migrated) == 0

    def test_migrate_clusters_creates_crdt_service_if_none(self):
        """Test that CrdtService is created if not provided."""
        handler = StrokeHandler()

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = None

        anchor = self._make_mock_anchor()
        cluster = self._make_mock_cluster(anchor=anchor)

        # Should not raise even without crdt_service
        migrated = handler.migrate_clusters(
            clusters=[cluster],
            old_text="old",
            new_text="new",
            context_resolver=mock_resolver,
        )

        assert len(migrated) == 0


class TestSerializeForPage:
    """Tests for serialize_for_page()."""

    def _make_mock_bundle(self, anchor_offset: int) -> MockBundle:
        """Create mock bundle with anchor offset."""
        return MockBundle(
            node_id=CrdtId(2, anchor_offset),
            anchor_offset=anchor_offset,
        )

    def _make_cluster_with_offset(self, anchor_offset: int) -> StrokeCluster:
        """Create cluster with bundle at given offset."""
        bundle = self._make_mock_bundle(anchor_offset)
        mock_line_block = MagicMock()
        stroke = Stroke(
            stroke_id=CrdtId(2, 1),
            points=[],
            bounding_box=(0, 0, 50, 50),
            color=0,
            tool=0,
            thickness=2.0,
            tree_node_id=CrdtId(2, 1),
            line_block=mock_line_block,
            bundle=bundle,
        )
        cluster = StrokeCluster(
            cluster_id=f"cluster_{anchor_offset}",
            strokes=[stroke],
            bounding_box=(0, 0, 100, 100),
        )
        cluster._bundles = [bundle]
        return cluster

    def test_serialize_for_page_filters_by_offset(self):
        """Test that only clusters in page range are serialized."""
        handler = StrokeHandler()

        # Create clusters at different offsets
        cluster_100 = self._make_cluster_with_offset(100)
        cluster_300 = self._make_cluster_with_offset(300)
        cluster_500 = self._make_cluster_with_offset(500)

        # Page projection for offset range 200-400
        page = PageProjection(
            page_index=1,
            page_uuid="test-uuid",
            doc_char_start=200,
            doc_char_end=400,
        )

        # Mock CrdtService to return bundle as-is
        mock_crdt_service = MagicMock(spec=CrdtService)
        mock_crdt_service.prepare_bundle_for_page.side_effect = lambda b: b

        blocks = handler.serialize_for_page(
            clusters=[cluster_100, cluster_300, cluster_500],
            page_projection=page,
            crdt_service=mock_crdt_service,
        )

        # Only cluster_300 should be serialized (offset 300 in range 200-400)
        assert len(blocks) == 1
        assert blocks[0] == "block_300"

    def test_serialize_for_page_no_clusters_in_range(self):
        """Test empty result when no clusters in page range."""
        handler = StrokeHandler()

        cluster = self._make_cluster_with_offset(100)

        # Page projection for offset range 500-600 (no clusters)
        page = PageProjection(
            page_index=2,
            page_uuid="test-uuid",
            doc_char_start=500,
            doc_char_end=600,
        )

        mock_crdt_service = MagicMock(spec=CrdtService)

        blocks = handler.serialize_for_page(
            clusters=[cluster],
            page_projection=page,
            crdt_service=mock_crdt_service,
        )

        assert len(blocks) == 0
        mock_crdt_service.prepare_bundle_for_page.assert_not_called()

    def test_serialize_for_page_skips_no_anchor(self):
        """Test that clusters without anchor offset are skipped."""
        handler = StrokeHandler()

        # Create cluster with no anchor offset
        bundle = MockBundle(node_id=CrdtId(2, 1), anchor_offset=None)
        mock_line_block = MagicMock()
        stroke = Stroke(
            stroke_id=CrdtId(2, 1),
            points=[],
            bounding_box=(0, 0, 50, 50),
            color=0,
            tool=0,
            thickness=2.0,
            tree_node_id=CrdtId(2, 1),
            line_block=mock_line_block,
            bundle=bundle,
        )
        cluster = StrokeCluster(
            cluster_id="no_anchor",
            strokes=[stroke],
            bounding_box=(0, 0, 100, 100),
        )
        cluster._bundles = [bundle]

        page = PageProjection(
            page_index=0,
            page_uuid="test-uuid",
            doc_char_start=0,
            doc_char_end=1000,
        )

        mock_crdt_service = MagicMock(spec=CrdtService)

        blocks = handler.serialize_for_page(
            clusters=[cluster],
            page_projection=page,
            crdt_service=mock_crdt_service,
        )

        assert len(blocks) == 0

    def test_serialize_for_page_creates_crdt_service_if_none(self):
        """Test that CrdtService is created if not provided."""
        handler = StrokeHandler()

        cluster = self._make_cluster_with_offset(50)

        page = PageProjection(
            page_index=0,
            page_uuid="test-uuid",
            doc_char_start=0,
            doc_char_end=100,
        )

        # Use real bundle that has to_raw_blocks
        # This will fail because MockBundle doesn't have prepare_bundle_for_page compatibility
        # But we can test that it doesn't raise with None crdt_service
        with patch(
            "rock_paper_sync.annotations.services.crdt_service.CrdtService"
        ) as mock_crdt_class:
            mock_instance = MagicMock()
            mock_instance.prepare_bundle_for_page.return_value = cluster.bundles[0]
            mock_crdt_class.return_value = mock_instance

            handler.serialize_for_page(
                clusters=[cluster],
                page_projection=page,
            )

            # Verify CrdtService was instantiated
            mock_crdt_class.assert_called_once()


class TestUpdateAnchorContext:
    """Tests for _update_anchor_context()."""

    def test_update_anchor_preserves_content(self):
        """Test that content fields are preserved."""
        handler = StrokeHandler()

        old_anchor = AnchorContext(
            content_hash="hash123",
            text_content="original text",
            paragraph_index=5,
            section_path=("section1", "section2"),
            context_before="before context",
            context_after="after context",
            y_position_hint=100.0,
            page_hint=2,
        )

        mock_resolved = MagicMock()
        mock_resolved.start_offset = 200

        new_anchor = handler._update_anchor_context(old_anchor, mock_resolved)

        # Preserved fields
        assert new_anchor.content_hash == "hash123"
        assert new_anchor.text_content == "original text"
        assert new_anchor.section_path == ("section1", "section2")
        assert new_anchor.context_before == "before context"
        assert new_anchor.context_after == "after context"
        assert new_anchor.y_position_hint == 100.0
        assert new_anchor.page_hint == 2

        # Reset fields
        assert new_anchor.paragraph_index is None
        assert new_anchor.line_range is None
