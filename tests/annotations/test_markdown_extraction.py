"""Tests for markdown extraction (Phase 3).

Tests handler-based extraction of annotations from markdown:
- HighlightHandler: Extract highlights in mark, bold, italic formats
- StrokeHandler: Extract OCR text in footnote, comment formats
- RenderConfig: Configurable rendering styles
"""

from rock_paper_sync.annotations.core.data_types import ExtractedAnnotation, RenderConfig
from rock_paper_sync.annotations.handlers.highlight_handler import HighlightHandler
from rock_paper_sync.annotations.handlers.stroke_handler import StrokeHandler


class TestHighlightExtraction:
    """Tests for HighlightHandler.extract_from_markdown()."""

    def test_extract_mark_style(self):
        """Test extracting highlights with <mark> tags."""
        handler = HighlightHandler()
        config = RenderConfig(highlight_style="mark")

        paragraph = "This is a <mark>highlighted phrase</mark> in the text."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 1
        assert extracted[0].text == "highlighted phrase"
        assert extracted[0].annotation_type == "highlight"
        assert extracted[0].start_offset == 10
        # end_offset includes the full match (opening tag + text + closing tag)
        assert extracted[0].end_offset == 41

    def test_extract_bold_style(self):
        """Test extracting highlights with **bold** format."""
        handler = HighlightHandler()
        config = RenderConfig(highlight_style="bold")

        paragraph = "This is a **highlighted phrase** in the text."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 1
        assert extracted[0].text == "highlighted phrase"
        assert extracted[0].annotation_type == "highlight"

    def test_extract_italic_style(self):
        """Test extracting highlights with *italic* format."""
        handler = HighlightHandler()
        config = RenderConfig(highlight_style="italic")

        paragraph = "This is a *highlighted phrase* in the text."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 1
        assert extracted[0].text == "highlighted phrase"
        assert extracted[0].annotation_type == "highlight"

    def test_extract_multiple_highlights(self):
        """Test extracting multiple highlights from same paragraph."""
        handler = HighlightHandler()
        config = RenderConfig(highlight_style="mark")

        paragraph = "First <mark>highlight</mark> and second <mark>highlight</mark> here."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 2
        assert extracted[0].text == "highlight"
        assert extracted[1].text == "highlight"

    def test_extract_no_highlights(self):
        """Test paragraph with no highlights."""
        handler = HighlightHandler()
        config = RenderConfig(highlight_style="mark")

        paragraph = "Plain text with no highlights."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 0

    def test_extract_nested_markup(self):
        """Test extracting highlight with nested content."""
        handler = HighlightHandler()
        config = RenderConfig(highlight_style="mark")

        paragraph = "Text with <mark>complex phrase with words</mark> highlighted."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 1
        assert extracted[0].text == "complex phrase with words"

    def test_extract_multiline_highlight(self):
        """Test extracting highlight spanning multiple words."""
        handler = HighlightHandler()
        config = RenderConfig(highlight_style="mark")

        paragraph = "Start <mark>highlighted section that continues across words</mark> end."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 1
        assert extracted[0].text == "highlighted section that continues across words"


class TestStrokeExtraction:
    """Tests for StrokeHandler.extract_from_markdown()."""

    def test_extract_comment_style(self):
        """Test extracting OCR text with HTML comment format."""
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="comment")

        paragraph = "Paragraph with <!-- OCR: handwritten text --> in it."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 1
        assert extracted[0].text == "handwritten text"
        assert extracted[0].annotation_type == "stroke"

    def test_extract_footnote_style(self):
        """Test extracting OCR text with footnote format.

        Note: Footnote pattern captures all text before the marker.
        In practice, rendering should separate OCR text from surrounding text.
        """
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="footnote")

        # Realistic usage: OCR text followed immediately by footnote
        paragraph = "Regular text. handwritten text[^1]\n\n[^1]: OCR confidence 0.95"
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 1
        # Pattern captures text up to first bracket, so includes "Regular text. handwritten text"
        # This is a known limitation of footnote style - use comment style for precise delimiting
        assert "handwritten text" in extracted[0].text
        assert extracted[0].annotation_type == "stroke"

    def test_extract_multiple_strokes_comment(self):
        """Test extracting multiple OCR texts (comment style)."""
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="comment")

        paragraph = "First <!-- OCR: text one --> and second <!-- OCR: text two --> here."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 2
        assert extracted[0].text == "text one"
        assert extracted[1].text == "text two"

    def test_extract_multiple_strokes_footnote(self):
        """Test extracting multiple OCR texts (footnote style)."""
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="footnote")

        paragraph = "First text[^1] and second text[^2] here.\n\n[^1]: OCR\n[^2]: OCR"
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 2
        assert extracted[0].text == "First text"
        assert extracted[1].text == "and second text"

    def test_extract_no_strokes(self):
        """Test paragraph with no strokes."""
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="comment")

        paragraph = "Plain text with no OCR annotations."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 0

    def test_extract_ocr_with_special_chars(self):
        """Test extracting OCR with special characters."""
        handler = StrokeHandler()
        config = RenderConfig(stroke_style="comment")

        paragraph = "Text with <!-- OCR: hello, world! --> punctuation."
        extracted = handler.extract_from_markdown(paragraph, config)

        assert len(extracted) == 1
        assert extracted[0].text == "hello, world!"


class TestMixedAnnotations:
    """Tests for paragraphs with mixed annotation types."""

    def test_extract_highlights_and_strokes(self):
        """Test extracting both highlights and strokes from same paragraph."""
        highlight_handler = HighlightHandler()
        stroke_handler = StrokeHandler()
        config = RenderConfig(highlight_style="mark", stroke_style="comment")

        paragraph = "Text with <mark>highlight</mark> and <!-- OCR: handwritten --> parts."

        highlights = highlight_handler.extract_from_markdown(paragraph, config)
        strokes = stroke_handler.extract_from_markdown(paragraph, config)

        assert len(highlights) == 1
        assert highlights[0].text == "highlight"

        assert len(strokes) == 1
        assert strokes[0].text == "handwritten"

    def test_different_configs_for_handlers(self):
        """Test handlers respecting their own config settings."""
        highlight_handler = HighlightHandler()

        # Different configs
        config1 = RenderConfig(highlight_style="mark", stroke_style="comment")
        config2 = RenderConfig(highlight_style="bold", stroke_style="footnote")

        paragraph_mark = "Text with <mark>highlight</mark> here."
        paragraph_bold = "Text with **highlight** here."

        # Mark style extracts mark
        extracted_mark = highlight_handler.extract_from_markdown(paragraph_mark, config1)
        assert len(extracted_mark) == 1

        # Bold style extracts bold
        extracted_bold = highlight_handler.extract_from_markdown(paragraph_bold, config2)
        assert len(extracted_bold) == 1

        # Mark style doesn't extract bold
        extracted_wrong = highlight_handler.extract_from_markdown(paragraph_bold, config1)
        assert len(extracted_wrong) == 0


class TestExtractedAnnotation:
    """Tests for ExtractedAnnotation data structure."""

    def test_extracted_annotation_fields(self):
        """Test ExtractedAnnotation has correct fields."""
        extracted = ExtractedAnnotation(
            text="sample text", annotation_type="highlight", start_offset=10, end_offset=25
        )

        assert extracted.text == "sample text"
        assert extracted.annotation_type == "highlight"
        assert extracted.start_offset == 10
        assert extracted.end_offset == 25

    def test_extracted_annotation_defaults(self):
        """Test ExtractedAnnotation default values."""
        extracted = ExtractedAnnotation(text="text", annotation_type="stroke")

        assert extracted.start_offset == -1
        assert extracted.end_offset == -1


class TestRenderConfig:
    """Tests for RenderConfig."""

    def test_render_config_defaults(self):
        """Test RenderConfig default values."""
        config = RenderConfig()

        assert config.highlight_style == "mark"
        assert config.stroke_style == "comment"

    def test_render_config_custom(self):
        """Test RenderConfig with custom values."""
        config = RenderConfig(highlight_style="bold", stroke_style="footnote")

        assert config.highlight_style == "bold"
        assert config.stroke_style == "footnote"
