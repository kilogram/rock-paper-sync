"""Tests for AnnotationMerger and related merging functionality.

Tests the AnnotationMerger orchestration layer which coordinates
annotation migration across document versions.
"""

from unittest.mock import MagicMock

import pytest

from rock_paper_sync.annotations.document_model import (
    AnchorContext,
    ContextResolver,
    DocumentAnnotation,
    DocumentModel,
    MigrationReport,
    ResolvedAnchorContext,
)
from rock_paper_sync.annotations.merging import (
    AnnotationMerger,
    MergeContext,
    MergeResult,
)


class TestMergeContext:
    """Tests for MergeContext dataclass."""

    def test_merge_context_is_frozen(self):
        """Test that MergeContext is immutable."""
        old_model = MagicMock(spec=DocumentModel)
        new_model = MagicMock(spec=DocumentModel)

        context = MergeContext(old_model=old_model, new_model=new_model)

        # Should raise FrozenInstanceError (or similar) when trying to modify
        with pytest.raises(Exception):  # dataclass frozen raises
            context.old_model = MagicMock()

    def test_merge_context_stores_models(self):
        """Test that MergeContext stores the document models."""
        old_model = MagicMock(spec=DocumentModel)
        new_model = MagicMock(spec=DocumentModel)

        context = MergeContext(old_model=old_model, new_model=new_model)

        assert context.old_model is old_model
        assert context.new_model is new_model


class TestMergeResult:
    """Tests for MergeResult dataclass."""

    def test_merge_result_success_rate(self):
        """Test success_rate property delegates to report."""
        mock_model = MagicMock(spec=DocumentModel)
        mock_report = MagicMock(spec=MigrationReport)
        mock_report.success_rate = 0.85

        result = MergeResult(merged_model=mock_model, report=mock_report)

        assert result.success_rate == 0.85

    def test_merge_result_migrated_count(self):
        """Test migrated_count property."""
        mock_model = MagicMock(spec=DocumentModel)
        mock_report = MagicMock(spec=MigrationReport)
        mock_report.migrations = [1, 2, 3]  # 3 migrations

        result = MergeResult(merged_model=mock_model, report=mock_report)

        assert result.migrated_count == 3

    def test_merge_result_orphan_count(self):
        """Test orphan_count property."""
        mock_model = MagicMock(spec=DocumentModel)
        mock_report = MagicMock(spec=MigrationReport)
        mock_report.orphans = [1, 2]  # 2 orphans

        result = MergeResult(merged_model=mock_model, report=mock_report)

        assert result.orphan_count == 2


class TestAnnotationMerger:
    """Tests for AnnotationMerger class."""

    def _make_mock_annotation(
        self,
        annotation_id: str = "test-id",
        annotation_type: str = "highlight",
        cluster_id: str | None = None,
    ) -> DocumentAnnotation:
        """Create a mock DocumentAnnotation."""
        anchor = AnchorContext(
            content_hash="hash123",
            text_content="test content",
            paragraph_index=0,
        )
        return DocumentAnnotation(
            annotation_id=annotation_id,
            annotation_type=annotation_type,
            anchor_context=anchor,
            cluster_id=cluster_id,
        )

    def _make_document_model(
        self,
        full_text: str = "Test document text",
        annotations: list[DocumentAnnotation] | None = None,
    ) -> DocumentModel:
        """Create a DocumentModel for testing."""
        return DocumentModel(
            paragraphs=[],
            content_blocks=[],
            full_text=full_text,
            annotations=annotations or [],
            geometry=None,  # No geometry to skip layout building
        )

    def test_merger_with_no_annotations(self):
        """Test merging when old model has no annotations."""
        old_model = self._make_document_model(annotations=[])
        new_model = self._make_document_model(full_text="New text")

        merger = AnnotationMerger(resolver=ContextResolver())
        result = merger.merge(MergeContext(old_model=old_model, new_model=new_model))

        assert len(result.merged_model.annotations) == 0
        assert result.success_rate == 1.0  # 0/0 = 100%

    def test_merger_creates_default_resolver(self):
        """Test that merger creates resolver if not provided."""
        old_model = self._make_document_model(annotations=[])
        new_model = self._make_document_model()

        merger = AnnotationMerger()  # No resolver provided
        result = merger.merge(MergeContext(old_model=old_model, new_model=new_model))

        # Should complete without error
        assert result.merged_model is not None

    def test_merger_with_custom_resolver(self):
        """Test that merger uses provided resolver."""
        old_model = self._make_document_model(
            full_text="Test content here",
            annotations=[self._make_mock_annotation()],
        )
        new_model = self._make_document_model(full_text="Test content here")

        mock_resolver = MagicMock(spec=ContextResolver)
        mock_resolution = ResolvedAnchorContext(
            start_offset=0,
            end_offset=12,
            confidence=0.9,
            match_type="exact",
        )
        mock_resolver.resolve.return_value = mock_resolution

        merger = AnnotationMerger(resolver=mock_resolver)
        result = merger.merge(MergeContext(old_model=old_model, new_model=new_model))

        # Should have called our resolver
        mock_resolver.resolve.assert_called()
        assert result.migrated_count == 1

    def test_merger_orphans_unresolvable_annotations(self):
        """Test that unresolvable annotations become orphans."""
        old_model = self._make_document_model(
            full_text="Old text",
            annotations=[self._make_mock_annotation()],
        )
        new_model = self._make_document_model(full_text="Completely different text")

        mock_resolver = MagicMock(spec=ContextResolver)
        mock_resolver.resolve.return_value = None  # Cannot resolve

        merger = AnnotationMerger(resolver=mock_resolver)
        result = merger.merge(MergeContext(old_model=old_model, new_model=new_model))

        assert result.migrated_count == 0
        assert result.orphan_count == 1

    def test_merger_handles_clustered_annotations(self):
        """Test that clustered annotations follow leader resolution."""
        anno1 = self._make_mock_annotation(annotation_id="anno-1", cluster_id="cluster-A")
        anno2 = self._make_mock_annotation(annotation_id="anno-2", cluster_id="cluster-A")
        old_model = self._make_document_model(
            full_text="Original text with content",
            annotations=[anno1, anno2],
        )
        new_model = self._make_document_model(full_text="New text with content")

        mock_resolver = MagicMock(spec=ContextResolver)
        mock_resolution = ResolvedAnchorContext(
            start_offset=5,
            end_offset=17,
            confidence=0.95,
            match_type="exact",
        )
        mock_resolver.resolve.return_value = mock_resolution

        merger = AnnotationMerger(resolver=mock_resolver)
        result = merger.merge(MergeContext(old_model=old_model, new_model=new_model))

        # Both annotations should be migrated using the leader's resolution
        assert result.migrated_count == 2
        assert result.orphan_count == 0

    def test_merger_preserves_cluster_id(self):
        """Test that migrated annotations preserve their cluster_id."""
        anno = self._make_mock_annotation(annotation_id="anno-1", cluster_id="my-cluster")
        old_model = self._make_document_model(
            full_text="Test content",
            annotations=[anno],
        )
        new_model = self._make_document_model(full_text="Test content")

        mock_resolver = MagicMock(spec=ContextResolver)
        mock_resolution = ResolvedAnchorContext(
            start_offset=0,
            end_offset=12,
            confidence=0.9,
            match_type="exact",
        )
        mock_resolver.resolve.return_value = mock_resolution

        merger = AnnotationMerger(resolver=mock_resolver)
        result = merger.merge(MergeContext(old_model=old_model, new_model=new_model))

        assert result.merged_model.annotations[0].cluster_id == "my-cluster"


class TestAnnotationMergerIntegration:
    """Integration tests for AnnotationMerger usage patterns."""

    def _make_document_model(
        self,
        full_text: str = "Test document text",
        annotations: list[DocumentAnnotation] | None = None,
    ) -> DocumentModel:
        """Create a DocumentModel for testing."""
        return DocumentModel(
            paragraphs=[],
            content_blocks=[],
            full_text=full_text,
            annotations=annotations or [],
            geometry=None,
        )

    def test_merge_with_default_resolver(self):
        """Test merging with default ContextResolver."""
        old_model = self._make_document_model(annotations=[])
        new_model = self._make_document_model()

        # Create merger with default resolver
        merger = AnnotationMerger(resolver=ContextResolver())
        context = MergeContext(old_model=old_model, new_model=new_model)
        result = merger.merge(context)

        assert result.merged_model is not None
        assert result.report is not None

    def test_merge_with_custom_resolver(self):
        """Test merging with custom resolver."""
        old_model = self._make_document_model(annotations=[])
        new_model = self._make_document_model()

        mock_resolver = MagicMock(spec=ContextResolver)
        merger = AnnotationMerger(resolver=mock_resolver)
        context = MergeContext(old_model=old_model, new_model=new_model)
        result = merger.merge(context)

        assert result.merged_model is not None

    def test_merge_result_structure(self):
        """Test that merge returns a MergeResult with correct structure."""
        old_model = self._make_document_model(annotations=[])
        new_model = self._make_document_model()

        merger = AnnotationMerger(resolver=ContextResolver())
        context = MergeContext(old_model=old_model, new_model=new_model)
        result = merger.merge(context)

        assert isinstance(result, MergeResult)
        assert isinstance(result.merged_model, DocumentModel)
        assert isinstance(result.report, MigrationReport)
