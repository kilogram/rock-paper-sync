"""Unit tests for AnnotationProcessor.

Tests the processor's orchestration logic:
- Handler registration
- Annotation mapping pipeline
- Error handling for missing files
- Database connection management
"""

from unittest.mock import MagicMock

from rock_paper_sync.annotations.core.processor import AnnotationProcessor
from rock_paper_sync.parser import BlockType, ContentBlock


class TestAnnotationProcessorInit:
    """Tests for processor initialization."""

    def test_init_without_db(self):
        """Processor initializes without database."""
        processor = AnnotationProcessor()
        assert processor.handlers == {}
        assert processor.db_path is None
        assert processor.db_connection is None

    def test_init_with_db(self, tmp_path):
        """Processor creates database connection when path provided."""
        db_path = tmp_path / "test.db"
        processor = AnnotationProcessor(db_path=db_path)
        assert processor.db_path == db_path
        assert processor.db_connection is not None
        processor.close()


class TestHandlerRegistration:
    """Tests for handler registration."""

    def test_register_handler(self):
        """Handler is registered by annotation type."""
        processor = AnnotationProcessor()
        mock_handler = MagicMock()
        mock_handler.annotation_type = "highlight"

        processor.register_handler(mock_handler)

        assert "highlight" in processor.handlers
        assert processor.handlers["highlight"] == mock_handler

    def test_register_multiple_handlers(self):
        """Multiple handlers can be registered."""
        processor = AnnotationProcessor()

        highlight_handler = MagicMock()
        highlight_handler.annotation_type = "highlight"

        stroke_handler = MagicMock()
        stroke_handler.annotation_type = "stroke"

        processor.register_handler(highlight_handler)
        processor.register_handler(stroke_handler)

        assert len(processor.handlers) == 2
        assert "highlight" in processor.handlers
        assert "stroke" in processor.handlers

    def test_register_handler_initializes_schema(self, tmp_path):
        """Handler schema is initialized when db connection exists."""
        db_path = tmp_path / "test.db"
        processor = AnnotationProcessor(db_path=db_path)

        mock_handler = MagicMock()
        mock_handler.annotation_type = "test"

        processor.register_handler(mock_handler)

        mock_handler.init_state_schema.assert_called_once_with(processor.db_connection)
        processor.close()


class TestMapAnnotationsToParagraphs:
    """Tests for annotation mapping."""

    def test_missing_rm_file_returns_empty(self, tmp_path):
        """Returns empty dict when .rm file doesn't exist."""
        processor = AnnotationProcessor()
        rm_path = tmp_path / "nonexistent.rm"
        blocks = [ContentBlock(text="Test paragraph", type=BlockType.PARAGRAPH, level=0)]

        result = processor.map_annotations_to_paragraphs(rm_path, blocks)

        assert result == {}

    def test_no_handlers_returns_empty(self, tmp_path):
        """Returns empty dict when no handlers registered."""
        processor = AnnotationProcessor()
        rm_path = tmp_path / "test.rm"
        rm_path.write_bytes(b"")  # Create empty file
        blocks = [ContentBlock(text="Test", type=BlockType.PARAGRAPH, level=0)]

        result = processor.map_annotations_to_paragraphs(rm_path, blocks)

        assert result == {}

    def test_handler_detects_no_annotations(self, tmp_path):
        """Handler that detects no annotations contributes nothing."""
        processor = AnnotationProcessor()
        rm_path = tmp_path / "test.rm"
        rm_path.write_bytes(b"")

        mock_handler = MagicMock()
        mock_handler.annotation_type = "highlight"
        mock_handler.detect.return_value = []  # No annotations

        processor.register_handler(mock_handler)
        blocks = [ContentBlock(text="Test", type=BlockType.PARAGRAPH, level=0)]

        result = processor.map_annotations_to_paragraphs(rm_path, blocks)

        assert result == {}
        mock_handler.detect.assert_called_once_with(rm_path)
        mock_handler.map.assert_not_called()  # map() not called if no annotations

    def test_handler_maps_annotations(self, tmp_path):
        """Handler detects and maps annotations to paragraphs."""
        processor = AnnotationProcessor()
        rm_path = tmp_path / "test.rm"
        rm_path.write_bytes(b"")

        mock_annotation = MagicMock()
        mock_handler = MagicMock()
        mock_handler.annotation_type = "highlight"
        mock_handler.detect.return_value = [mock_annotation]
        mock_handler.map.return_value = {0: [mock_annotation]}  # Map to paragraph 0

        processor.register_handler(mock_handler)
        blocks = [ContentBlock(text="Test", type=BlockType.PARAGRAPH, level=0)]

        result = processor.map_annotations_to_paragraphs(rm_path, blocks)

        assert 0 in result
        assert result[0].highlights == 1
        assert result[0].strokes == 0

    def test_stroke_handler_increments_strokes(self, tmp_path):
        """Stroke handler increments stroke count."""
        processor = AnnotationProcessor()
        rm_path = tmp_path / "test.rm"
        rm_path.write_bytes(b"")

        mock_annotation = MagicMock()
        mock_handler = MagicMock()
        mock_handler.annotation_type = "stroke"
        mock_handler.detect.return_value = [mock_annotation]
        mock_handler.map.return_value = {1: [mock_annotation]}

        processor.register_handler(mock_handler)
        blocks = [
            ContentBlock(text="First", type=BlockType.PARAGRAPH, level=0),
            ContentBlock(text="Second", type=BlockType.PARAGRAPH, level=0),
        ]

        result = processor.map_annotations_to_paragraphs(rm_path, blocks)

        assert 1 in result
        assert result[1].strokes == 1
        assert result[1].highlights == 0

    def test_multiple_handlers_accumulate(self, tmp_path):
        """Multiple handlers contribute to same paragraph."""
        processor = AnnotationProcessor()
        rm_path = tmp_path / "test.rm"
        rm_path.write_bytes(b"")
        blocks = [ContentBlock(text="Test", type=BlockType.PARAGRAPH, level=0)]

        # Highlight handler
        highlight_anno = MagicMock()
        highlight_handler = MagicMock()
        highlight_handler.annotation_type = "highlight"
        highlight_handler.detect.return_value = [highlight_anno, highlight_anno]
        highlight_handler.map.return_value = {0: [highlight_anno, highlight_anno]}

        # Stroke handler
        stroke_anno = MagicMock()
        stroke_handler = MagicMock()
        stroke_handler.annotation_type = "stroke"
        stroke_handler.detect.return_value = [stroke_anno]
        stroke_handler.map.return_value = {0: [stroke_anno]}

        processor.register_handler(highlight_handler)
        processor.register_handler(stroke_handler)

        result = processor.map_annotations_to_paragraphs(rm_path, blocks)

        assert 0 in result
        assert result[0].highlights == 2
        assert result[0].strokes == 1

    def test_file_like_object_returns_empty(self):
        """File-like objects currently return empty (not supported)."""
        import io

        processor = AnnotationProcessor()
        file_obj = io.BytesIO(b"test data")
        blocks = [ContentBlock(text="Test", type=BlockType.PARAGRAPH, level=0)]

        result = processor.map_annotations_to_paragraphs(file_obj, blocks)

        assert result == {}

    def test_layout_context_passed_to_handlers(self, tmp_path):
        """Layout context is passed through to handlers."""
        processor = AnnotationProcessor()
        rm_path = tmp_path / "test.rm"
        rm_path.write_bytes(b"")

        mock_annotation = MagicMock()
        mock_handler = MagicMock()
        mock_handler.annotation_type = "stroke"
        mock_handler.detect.return_value = [mock_annotation]
        mock_handler.map.return_value = {}

        mock_layout = MagicMock()
        processor.register_handler(mock_handler)
        blocks = [ContentBlock(text="Test", type=BlockType.PARAGRAPH, level=0)]

        processor.map_annotations_to_paragraphs(rm_path, blocks, layout_context=mock_layout)

        # Verify layout context was passed to map()
        mock_handler.map.assert_called_once()
        call_args = mock_handler.map.call_args
        assert call_args[0][3] == mock_layout  # 4th positional arg


class TestProcessorClose:
    """Tests for processor cleanup."""

    def test_close_without_db(self):
        """Close works without database."""
        processor = AnnotationProcessor()
        processor.close()  # Should not raise

    def test_close_with_db(self, tmp_path):
        """Close closes database connection."""
        db_path = tmp_path / "test.db"
        processor = AnnotationProcessor(db_path=db_path)
        assert processor.db_connection is not None

        processor.close()

        assert processor.db_connection is None
