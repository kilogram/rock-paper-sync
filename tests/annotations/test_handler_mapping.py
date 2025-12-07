"""Unit tests for annotation handler mapping and rendering.

Tests the map() and render() methods of HighlightHandler and StrokeHandler.
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


class TestHighlightHandlerRender:
    """Tests for HighlightHandler.render()."""

    @pytest.fixture
    def handler(self):
        return HighlightHandler()

    def test_render_empty_matches(self, handler):
        """Empty matches returns original content."""
        content = "Original paragraph"

        result = handler.render(0, [], content)

        assert result == content

    def test_render_single_highlight(self, handler):
        """Single highlight renders as HTML comment."""
        annotation = MagicMock()
        annotation.highlight = MagicMock()
        annotation.highlight.text = "important word"

        content = "This is the paragraph"

        result = handler.render(0, [annotation], content)

        assert "<!-- Highlights: important word -->" in result
        assert content in result

    def test_render_multiple_highlights(self, handler):
        """Multiple highlights joined with pipe."""
        anno1 = MagicMock()
        anno1.highlight = MagicMock()
        anno1.highlight.text = "first"

        anno2 = MagicMock()
        anno2.highlight = MagicMock()
        anno2.highlight.text = "second"

        content = "Paragraph content"

        result = handler.render(0, [anno1, anno2], content)

        assert "<!-- Highlights: first | second -->" in result

    def test_render_no_highlight_text(self, handler):
        """Annotations without highlight text are skipped."""
        annotation = MagicMock()
        annotation.highlight = None  # No highlight

        content = "Original"

        result = handler.render(0, [annotation], content)

        assert result == "Original"  # Unchanged


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
                "rock_paper_sync.annotations.handlers.stroke_handler.extract_text_origin"
            ) as mock_origin,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.build_parent_anchor_map"
            ) as mock_anchor,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.extract_text_blocks_from_rm"
            ) as mock_extract,
        ):
            mock_origin.return_value = MagicMock(x=-375, y=94)
            mock_anchor.return_value = {}
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
                "rock_paper_sync.annotations.handlers.stroke_handler.extract_text_origin"
            ) as mock_origin,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.build_parent_anchor_map"
            ) as mock_anchor,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.extract_text_blocks_from_rm"
            ) as mock_extract,
        ):
            mock_origin.return_value = MagicMock(x=-375, y=94)
            mock_anchor.return_value = {}
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
        annotation.parent_id = None  # Absolute coordinates

        blocks = [ContentBlock(text="Test", type=BlockType.PARAGRAPH, level=0)]

        with (
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.extract_text_origin"
            ) as mock_origin,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.build_parent_anchor_map"
            ) as mock_anchor,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.extract_text_blocks_from_rm"
            ) as mock_extract,
            patch(
                "rock_paper_sync.annotations.handlers.stroke_handler.find_nearest_paragraph_by_y"
            ) as mock_find,
        ):
            mock_origin.return_value = MagicMock(x=-375, y=94)
            mock_anchor.return_value = {}
            mock_extract.return_value = ([], 94.0)
            mock_find.return_value = 0

            result = handler.map([annotation], blocks, mock_rm_file)

        assert 0 in result
        assert annotation in result[0]


class TestStrokeHandlerRender:
    """Tests for StrokeHandler.render()."""

    @pytest.fixture
    def handler(self):
        return StrokeHandler()

    def test_render_empty_matches(self, handler):
        """Empty matches returns original content."""
        content = "Original paragraph"

        result = handler.render(0, [], content)

        assert result == content

    def test_render_strokes_without_ocr_processor(self, handler):
        """Strokes without OCR processor add annotation count marker."""
        annotation = MagicMock()
        annotation.stroke = MagicMock()

        content = "Original paragraph"

        result = handler.render(0, [annotation], content)

        # Should add handwritten annotation marker
        assert "<!-- 1 handwritten annotation(s) -->" in result
        assert content in result

    def test_render_multiple_strokes(self, handler):
        """Multiple strokes show correct count."""
        anno1 = MagicMock()
        anno2 = MagicMock()
        anno3 = MagicMock()

        content = "Original paragraph"

        result = handler.render(0, [anno1, anno2, anno3], content)

        assert "<!-- 3 handwritten annotation(s) -->" in result


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
            assert hasattr(handler, "render")
            assert hasattr(handler, "init_state_schema")
            assert hasattr(handler, "store_state")
            assert hasattr(handler, "load_state")
            assert hasattr(handler, "create_anchor")
            assert hasattr(handler, "extract_from_markdown")
