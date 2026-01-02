"""Unit tests for annotation handler mapping.

Tests the map() method of HighlightHandler and StrokeHandler.
These test error paths and edge cases that are hard to capture in replay tests.
"""

from unittest.mock import MagicMock, patch

import pytest

from rock_paper_sync.annotations import Annotation, Rectangle
from rock_paper_sync.annotations.handlers.highlight_handler import HighlightHandler
from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler
from rock_paper_sync.parser import BlockType, ContentBlock


class TestHighlightHandlerMap:
    """Tests for HighlightHandler.map()."""

    @pytest.fixture
    def handler(self):
        return HighlightHandler()

    @pytest.fixture
    def mock_rm_file(self, tmp_path):
        """Create a mock .rm file path."""
        rm_file = tmp_path / "test.rm"
        rm_file.write_bytes(b"")
        return rm_file

    def test_empty_annotations_returns_empty(self, handler, mock_rm_file):
        """Empty annotation list returns empty mapping."""
        blocks = [ContentBlock(text="Test paragraph", type=BlockType.PARAGRAPH, level=0)]

        result = handler.map([], blocks, mock_rm_file)

        assert result == {}

    def test_highlight_text_matching(self, handler, mock_rm_file):
        """Highlight is mapped by text matching."""
        # Create annotation with highlight text
        annotation = MagicMock(spec=Annotation)
        annotation.annotation_id = "test-id-123"
        annotation.highlight = MagicMock()
        annotation.highlight.text = "important text"
        annotation.bounding_box = None

        blocks = [
            ContentBlock(text="This is some text", type=BlockType.PARAGRAPH, level=0),
            ContentBlock(text="This has important text in it", type=BlockType.PARAGRAPH, level=0),
        ]

        with patch(
            "rock_paper_sync.annotations.handlers.highlight_handler.extract_text_blocks_from_rm"
        ) as mock_extract:
            mock_extract.return_value = ([], 94.0)  # Empty text blocks, default origin

            result = handler.map([annotation], blocks, mock_rm_file)

        assert 1 in result  # Matched to second paragraph
        assert annotation in result[1]

    def test_highlight_case_insensitive_match(self, handler, mock_rm_file):
        """Text matching is case-insensitive."""
        annotation = MagicMock(spec=Annotation)
        annotation.annotation_id = "test-id"
        annotation.highlight = MagicMock()
        annotation.highlight.text = "IMPORTANT"
        annotation.bounding_box = None

        blocks = [ContentBlock(text="This is important stuff", type=BlockType.PARAGRAPH, level=0)]

        with patch(
            "rock_paper_sync.annotations.handlers.highlight_handler.extract_text_blocks_from_rm"
        ) as mock_extract:
            mock_extract.return_value = ([], 94.0)

            result = handler.map([annotation], blocks, mock_rm_file)

        assert 0 in result

    def test_no_highlight_text_uses_position(self, handler, mock_rm_file):
        """Falls back to position matching when no highlight text."""
        annotation = MagicMock(spec=Annotation)
        annotation.annotation_id = "test-id"
        annotation.highlight = MagicMock()
        annotation.highlight.text = None  # No text
        annotation.bounding_box = Rectangle(x=100, y=150, w=50, h=20)

        blocks = [ContentBlock(text="Test paragraph", type=BlockType.PARAGRAPH, level=0)]

        with (
            patch(
                "rock_paper_sync.annotations.handlers.highlight_handler.extract_text_blocks_from_rm"
            ) as mock_extract,
            patch(
                "rock_paper_sync.annotations.handlers.highlight_handler.find_nearest_paragraph_by_y"
            ) as mock_find,
        ):
            mock_extract.return_value = ([], 94.0)
            mock_find.return_value = 0  # Map to paragraph 0

            result = handler.map([annotation], blocks, mock_rm_file)

        assert 0 in result
        mock_find.assert_called_once()

    def test_unmapped_highlight_logged(self, handler, mock_rm_file, caplog):
        """Warning logged when highlight cannot be mapped."""
        annotation = MagicMock(spec=Annotation)
        annotation.annotation_id = "unmapped-id"
        annotation.highlight = MagicMock()
        annotation.highlight.text = "nonexistent text xyz"
        annotation.bounding_box = None

        blocks = [
            ContentBlock(text="Completely different content", type=BlockType.PARAGRAPH, level=0)
        ]

        with patch(
            "rock_paper_sync.annotations.handlers.highlight_handler.extract_text_blocks_from_rm"
        ) as mock_extract:
            mock_extract.return_value = ([], 94.0)

            result = handler.map([annotation], blocks, mock_rm_file)

        assert result == {}  # No mappings
        # Warning should be logged
        assert any("Could not map" in record.message for record in caplog.records)


class TestStrokeHandlerMap:
    """Tests for StrokeHandler.map()."""

    @pytest.fixture
    def handler(self):
        return StrokeHandler()

    @pytest.fixture
    def mock_rm_file(self, tmp_path):
        rm_file = tmp_path / "test.rm"
        rm_file.write_bytes(b"")
        return rm_file

    def test_empty_annotations_returns_empty(self, handler, mock_rm_file):
        """Empty annotation list returns empty mapping."""
        blocks = [ContentBlock(text="Test", type=BlockType.PARAGRAPH, level=0)]

        with (
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.AnchorResolver.from_rm_file"
            ) as mock_resolver,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.extract_text_blocks_from_rm"
            ) as mock_extract,
        ):
            mock_resolver.return_value = MagicMock()
            mock_extract.return_value = ([], 94.0)

            result = handler.map([], blocks, mock_rm_file)

        assert result == {}

    def test_stroke_without_bbox_skipped(self, handler, mock_rm_file, caplog):
        """Stroke without bounding box is skipped with warning."""
        annotation = MagicMock()
        annotation.annotation_id = "no-bbox-id"
        annotation.stroke = MagicMock()
        annotation.stroke.bounding_box = None  # Missing bbox

        blocks = [ContentBlock(text="Test", type=BlockType.PARAGRAPH, level=0)]

        with (
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.AnchorResolver.from_rm_file"
            ) as mock_resolver,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.extract_text_blocks_from_rm"
            ) as mock_extract,
        ):
            mock_resolver.return_value = MagicMock()
            mock_extract.return_value = ([], 94.0)

            result = handler.map([annotation], blocks, mock_rm_file)

        assert result == {}
        assert any("missing bounding box" in record.message for record in caplog.records)

    def test_stroke_maps_to_paragraph(self, handler, mock_rm_file):
        """Stroke with bbox maps to nearest paragraph."""
        annotation = MagicMock()
        annotation.annotation_id = "stroke-id"
        annotation.stroke = MagicMock()
        annotation.stroke.bounding_box = Rectangle(x=100, y=150, w=50, h=30)
        annotation.parent_id = None  # No parent - treated as absolute coordinates

        blocks = [ContentBlock(text="Test", type=BlockType.PARAGRAPH, level=0)]

        with (
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.AnchorResolver.from_rm_file"
            ) as mock_resolver,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.extract_text_blocks_from_rm"
            ) as mock_extract,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.find_nearest_paragraph_by_y"
            ) as mock_find,
        ):
            mock_resolver.return_value = MagicMock()
            mock_extract.return_value = ([], 94.0)
            mock_find.return_value = 0

            result = handler.map([annotation], blocks, mock_rm_file)

        assert 0 in result
        assert annotation in result[0]


class TestHandlerIntegration:
    """Integration tests for handler patterns."""

    def test_handler_annotation_type(self):
        """Handlers report correct annotation type."""
        highlight = HighlightHandler()
        stroke = StrokeHandler()

        assert highlight.annotation_type == "highlight"
        assert stroke.annotation_type == "stroke"

    def test_handlers_implement_protocol(self):
        """Handlers implement required protocol methods."""

        highlight = HighlightHandler()
        stroke = StrokeHandler()

        # Check required methods exist
        for handler in [highlight, stroke]:
            assert hasattr(handler, "annotation_type")
            assert hasattr(handler, "detect")
            assert hasattr(handler, "map")
            assert hasattr(handler, "create_anchor")
            assert hasattr(handler, "extract_from_markdown")
