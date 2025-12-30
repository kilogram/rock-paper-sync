"""Tests for AnnotationMerger and related merging functionality.

Tests the AnnotationMerger orchestration layer which coordinates
annotation migration across document versions.
"""

from unittest.mock import MagicMock

import pytest

from rock_paper_sync.annotations.document_model import (
    AnchorContext,
    DocumentAnnotation,
    DocumentModel,
    MigrationReport,
)
from rock_paper_sync.annotations.services.merger import (
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
        full_text: str = "Test content here",
        annotation_id: str = "test-id",
        annotation_type: str = "highlight",
        cluster_id: str | None = None,
    ) -> DocumentAnnotation:
        """Create a mock DocumentAnnotation with proper anchor."""
        # Create anchor from the actual text
        start = full_text.find("content")
        if start == -1:
            start = 0
        end = min(start + 7, len(full_text))

        anchor = AnchorContext.from_text_span(
            full_text,
            start,
            end,
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

        merger = AnnotationMerger(fuzzy_threshold=0.8)
        result = merger.merge(MergeContext(old_model=old_model, new_model=new_model))

        assert len(result.merged_model.annotations) == 0
        assert result.success_rate == 1.0  # 0/0 = 100%

    def test_merger_uses_default_threshold(self):
        """Test that merger uses default fuzzy threshold if not provided."""
        old_model = self._make_document_model(annotations=[])
        new_model = self._make_document_model()

        merger = AnnotationMerger()  # Uses default fuzzy_threshold=0.8
        result = merger.merge(MergeContext(old_model=old_model, new_model=new_model))

        # Should complete without error
        assert result.merged_model is not None

    def test_merger_with_custom_threshold(self):
        """Test that merger uses provided fuzzy threshold."""
        full_text = "Test content here"
        old_model = self._make_document_model(
            full_text=full_text,
            annotations=[self._make_mock_annotation(full_text=full_text)],
        )
        new_model = self._make_document_model(full_text=full_text)

        # Use high threshold (0.95) for strict matching
        merger = AnnotationMerger(fuzzy_threshold=0.95)
        result = merger.merge(MergeContext(old_model=old_model, new_model=new_model))

        # Should successfully migrate with exact match
        assert result.migrated_count == 1

    def test_merger_orphans_unresolvable_annotations(self):
        """Test that unresolvable annotations become orphans."""
        old_model = self._make_document_model(
            full_text="Old text",
            annotations=[self._make_mock_annotation()],
        )
        new_model = self._make_document_model(full_text="Completely different text")

        merger = AnnotationMerger(fuzzy_threshold=0.8)
        result = merger.merge(MergeContext(old_model=old_model, new_model=new_model))

        # Anchor cannot resolve in completely different text
        assert result.migrated_count == 0
        assert result.orphan_count == 1

    def test_merger_handles_clustered_annotations(self):
        """Test that clustered annotations follow leader resolution."""
        old_text = "Original text with content"
        new_text = "New text with content"
        anno1 = self._make_mock_annotation(
            full_text=old_text, annotation_id="anno-1", cluster_id="cluster-A"
        )
        anno2 = self._make_mock_annotation(
            full_text=old_text, annotation_id="anno-2", cluster_id="cluster-A"
        )
        old_model = self._make_document_model(
            full_text=old_text,
            annotations=[anno1, anno2],
        )
        new_model = self._make_document_model(full_text=new_text)

        merger = AnnotationMerger(fuzzy_threshold=0.8)
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

        merger = AnnotationMerger(fuzzy_threshold=0.8)
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

    def test_merge_with_default_threshold(self):
        """Test merging with default fuzzy threshold."""
        old_model = self._make_document_model(annotations=[])
        new_model = self._make_document_model()

        # Create merger with default threshold (0.8)
        merger = AnnotationMerger()
        context = MergeContext(old_model=old_model, new_model=new_model)
        result = merger.merge(context)

        assert result.merged_model is not None
        assert result.report is not None

    def test_merge_with_custom_threshold(self):
        """Test merging with custom fuzzy threshold."""
        old_model = self._make_document_model(annotations=[])
        new_model = self._make_document_model()

        # Use strict threshold
        merger = AnnotationMerger(fuzzy_threshold=0.95)
        context = MergeContext(old_model=old_model, new_model=new_model)
        result = merger.merge(context)

        assert result.merged_model is not None

    def test_merge_result_structure(self):
        """Test that merge returns a MergeResult with correct structure."""
        old_model = self._make_document_model(annotations=[])
        new_model = self._make_document_model()

        merger = AnnotationMerger(fuzzy_threshold=0.8)
        context = MergeContext(old_model=old_model, new_model=new_model)
        result = merger.merge(context)

        assert isinstance(result, MergeResult)
        assert isinstance(result.merged_model, DocumentModel)
        assert isinstance(result.report, MigrationReport)
