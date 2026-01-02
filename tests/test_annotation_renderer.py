"""Tests for annotation renderer (M5 pull sync).

Tests the rendering of device annotations to markdown format.
"""

from rock_paper_sync.annotation_renderer import (
    AnnotationRenderer,
    RenderConfig,
    RenderResult,
    render_annotations_to_markdown,
)
from rock_paper_sync.annotations.document_model import (
    AnchorContext,
    DocumentAnnotation,
    DocumentModel,
)


class TestRenderConfig:
    """Tests for RenderConfig defaults."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = RenderConfig()
        assert config.highlight_style == "obsidian"
        assert config.stroke_style == "footnote"
        assert config.orphan_comment_location == "top"
        assert config.include_ocr_confidence is False


class TestHighlightRendering:
    """Tests for highlight annotation rendering."""

    def test_single_highlight(self) -> None:
        """Test rendering a single highlight."""
        content = "The quick brown fox jumps over the lazy dog."
        highlight = _make_highlight("quick brown fox", content)

        model = DocumentModel(paragraphs=[], annotations=[highlight], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.highlights_rendered == 1
        assert "==quick brown fox==" in result.content
        assert result.strokes_rendered == 0
        assert result.orphans_count == 0

    def test_multiple_highlights(self) -> None:
        """Test rendering multiple highlights."""
        content = "The quick brown fox jumps over the lazy dog."
        highlights = [
            _make_highlight("quick", content),
            _make_highlight("lazy", content),
        ]

        model = DocumentModel(paragraphs=[], annotations=highlights, full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.highlights_rendered == 2
        assert "==quick==" in result.content
        assert "==lazy==" in result.content

    def test_highlight_already_exists(self) -> None:
        """Test that already-highlighted text is not double-highlighted."""
        content = "The ==quick== brown fox."
        highlight = _make_highlight("quick", content)

        model = DocumentModel(paragraphs=[], annotations=[highlight], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        # Should not add another set of markers
        assert result.content.count("==quick==") == 1
        assert "====quick====" not in result.content

    def test_highlight_html_comment_style(self) -> None:
        """Test HTML comment highlight style."""
        content = "The quick brown fox."
        highlight = _make_highlight("quick", content)

        model = DocumentModel(paragraphs=[], annotations=[highlight], full_text=content)
        config = RenderConfig(highlight_style="html_comment")
        renderer = AnnotationRenderer(config)
        result = renderer.render(content, model)

        assert "<!-- HL -->quick<!-- /HL -->" in result.content

    def test_highlight_with_no_anchor(self) -> None:
        """Test highlight without anchor context is skipped."""
        content = "The quick brown fox."
        highlight = DocumentAnnotation(
            annotation_id="test-1",
            annotation_type="highlight",
            source_page_idx=0,
            anchor_context=None,
        )

        model = DocumentModel(paragraphs=[], annotations=[highlight], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        # Highlight without anchor is skipped
        assert "==" not in result.content

    def test_highlight_preserves_order(self) -> None:
        """Test that highlights don't corrupt positions of other highlights."""
        content = "Word1 Word2 Word3 Word4"
        highlights = [
            _make_highlight("Word1", content),
            _make_highlight("Word2", content),
            _make_highlight("Word3", content),
            _make_highlight("Word4", content),
        ]

        model = DocumentModel(paragraphs=[], annotations=highlights, full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.highlights_rendered == 4
        assert "==Word1== ==Word2== ==Word3== ==Word4==" in result.content


class TestStrokeRendering:
    """Tests for stroke annotation rendering."""

    def test_single_stroke_footnote(self) -> None:
        """Test rendering a stroke as footnote."""
        content = "The paragraph text here."
        stroke = _make_stroke("paragraph", content, ocr_text="My note")

        model = DocumentModel(paragraphs=[], annotations=[stroke], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.strokes_rendered == 1
        assert "[^stroke-1]" in result.content
        assert "[^stroke-1]: My note" in result.content

    def test_stroke_without_ocr(self) -> None:
        """Test stroke without OCR text renders as [handwriting]."""
        content = "The paragraph text here."
        stroke = _make_stroke("paragraph", content, ocr_text=None)

        model = DocumentModel(paragraphs=[], annotations=[stroke], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert "[handwriting]" in result.content

    def test_stroke_inline_style(self) -> None:
        """Test inline stroke style."""
        content = "The paragraph text here."
        stroke = _make_stroke("paragraph", content, ocr_text="Note")

        model = DocumentModel(paragraphs=[], annotations=[stroke], full_text=content)
        config = RenderConfig(stroke_style="inline")
        renderer = AnnotationRenderer(config)
        result = renderer.render(content, model)

        assert "*[Note]*" in result.content

    def test_stroke_comment_style(self) -> None:
        """Test HTML comment stroke style."""
        content = "The paragraph text here."
        stroke = _make_stroke("paragraph", content, ocr_text="Note")

        model = DocumentModel(paragraphs=[], annotations=[stroke], full_text=content)
        config = RenderConfig(stroke_style="comment")
        renderer = AnnotationRenderer(config)
        result = renderer.render(content, model)

        assert "<!-- stroke: Note -->" in result.content

    def test_multiple_strokes_with_footnotes(self) -> None:
        """Test multiple strokes get sequential footnotes."""
        content = "First paragraph. Second paragraph."
        strokes = [
            _make_stroke("First", content, ocr_text="Note 1"),
            _make_stroke("Second", content, ocr_text="Note 2"),
        ]

        model = DocumentModel(paragraphs=[], annotations=strokes, full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.strokes_rendered == 2
        # Both footnotes should be present (order may vary due to reverse processing)
        assert "[^stroke-1]" in result.content
        assert "[^stroke-2]" in result.content
        # Both notes should be in the footnote definitions
        assert "Note 1" in result.content
        assert "Note 2" in result.content


class TestOrphanRendering:
    """Tests for orphaned annotation rendering."""

    def test_single_orphan_comment(self) -> None:
        """Test orphan comment for single annotation."""
        content = "Document content."
        orphan = _make_highlight("deleted text", "deleted text here")

        model = DocumentModel(paragraphs=[], annotations=[], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model, orphaned_annotations=[orphan])

        assert result.orphans_count == 1
        assert "<!-- 1 orphaned annotation preserved in device file -->" in result.content

    def test_multiple_orphans_comment(self) -> None:
        """Test orphan comment for multiple annotations."""
        content = "Document content."
        orphans = [
            _make_highlight("deleted1", "deleted1"),
            _make_highlight("deleted2", "deleted2"),
            _make_stroke("deleted3", "deleted3"),
        ]

        model = DocumentModel(paragraphs=[], annotations=[], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model, orphaned_annotations=orphans)

        assert result.orphans_count == 3
        assert "<!-- 3 orphaned annotations preserved in device file -->" in result.content

    def test_orphan_comment_at_top(self) -> None:
        """Test orphan comment is added at top by default."""
        content = "# Document\n\nContent here."
        orphan = _make_highlight("deleted", "deleted")

        model = DocumentModel(paragraphs=[], annotations=[], full_text=content)
        config = RenderConfig(orphan_comment_location="top")
        renderer = AnnotationRenderer(config)
        result = renderer.render(content, model, orphaned_annotations=[orphan])

        assert result.content.startswith("<!-- 1 orphaned annotation")

    def test_orphan_comment_at_bottom(self) -> None:
        """Test orphan comment can be at bottom."""
        content = "# Document\n\nContent here."
        orphan = _make_highlight("deleted", "deleted")

        model = DocumentModel(paragraphs=[], annotations=[], full_text=content)
        config = RenderConfig(orphan_comment_location="bottom")
        renderer = AnnotationRenderer(config)
        result = renderer.render(content, model, orphaned_annotations=[orphan])

        # Orphan comment at bottom includes trailing newlines
        assert "<!-- 1 orphaned annotation preserved in device file -->" in result.content
        assert result.content.find("<!-- 1 orphaned") > result.content.find("Content here")

    def test_orphan_comment_updates_existing(self) -> None:
        """Test that existing orphan comment is updated."""
        content = "<!-- 2 orphaned annotations preserved in device file -->\n\n# Document"
        orphans = [
            _make_highlight("d1", "d1"),
            _make_highlight("d2", "d2"),
            _make_highlight("d3", "d3"),
        ]

        model = DocumentModel(paragraphs=[], annotations=[], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model, orphaned_annotations=orphans)

        # Should update to 3, not add another comment
        assert result.content.count("orphaned annotation") == 1
        assert "3 orphaned annotations" in result.content


class TestCombinedRendering:
    """Tests for rendering multiple annotation types together."""

    def test_highlights_and_strokes(self) -> None:
        """Test rendering both highlights and strokes."""
        content = "Important text here. Another sentence with notes."
        highlight = _make_highlight("Important", content)
        stroke = _make_stroke("notes", content, ocr_text="See also")

        model = DocumentModel(paragraphs=[], annotations=[highlight, stroke], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.highlights_rendered == 1
        assert result.strokes_rendered == 1
        assert "==Important==" in result.content
        assert "[^stroke-1]" in result.content

    def test_all_annotation_types(self) -> None:
        """Test rendering highlights, strokes, and orphans together."""
        content = "Some text. More text."
        highlight = _make_highlight("Some", content)
        stroke = _make_stroke("More", content, ocr_text="Note")
        orphan = _make_highlight("deleted", "deleted content")

        model = DocumentModel(paragraphs=[], annotations=[highlight, stroke], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model, orphaned_annotations=[orphan])

        assert result.highlights_rendered == 1
        assert result.strokes_rendered == 1
        assert result.orphans_count == 1
        assert "==Some==" in result.content
        assert "[^stroke-1]: Note" in result.content
        assert "orphaned annotation" in result.content


class TestRenderResult:
    """Tests for RenderResult dataclass."""

    def test_empty_result(self) -> None:
        """Test empty render result."""
        result = RenderResult(content="test")
        assert result.content == "test"
        assert result.highlights_rendered == 0
        assert result.strokes_rendered == 0
        assert result.orphans_count == 0
        assert result.orphan_details == []


class TestConvenienceFunction:
    """Tests for the module-level convenience function."""

    def test_render_annotations_to_markdown(self) -> None:
        """Test the convenience function."""
        content = "Highlight this text."
        highlight = _make_highlight("this", content)
        model = DocumentModel(paragraphs=[], annotations=[highlight], full_text=content)

        result = render_annotations_to_markdown(content, model)

        assert "==this==" in result.content
        assert result.highlights_rendered == 1


# Helper functions to create test annotations


def _make_highlight(text: str, content: str) -> DocumentAnnotation:
    """Create a highlight annotation for testing."""
    offset = content.find(text)
    if offset < 0:
        offset = 0
    anchor = AnchorContext.from_text_span(content, offset, offset + len(text))
    return DocumentAnnotation(
        annotation_id=f"highlight-{id(text)}",
        annotation_type="highlight",
        source_page_idx=0,
        anchor_context=anchor,
    )


def _make_stroke(anchor_text: str, content: str, ocr_text: str | None = None) -> DocumentAnnotation:
    """Create a stroke annotation for testing."""
    offset = content.find(anchor_text)
    if offset < 0:
        offset = 0
    anchor = AnchorContext.from_text_span(content, offset, offset + len(anchor_text))

    # Create stroke data with OCR text if provided
    stroke_data = None
    if ocr_text is not None:
        from dataclasses import dataclass

        @dataclass
        class MockStrokeData:
            ocr_text: str | None = None

        stroke_data = MockStrokeData(ocr_text=ocr_text)

    return DocumentAnnotation(
        annotation_id=f"stroke-{id(anchor_text)}",
        annotation_type="stroke",
        source_page_idx=0,
        anchor_context=anchor,
        stroke_data=stroke_data,
    )
