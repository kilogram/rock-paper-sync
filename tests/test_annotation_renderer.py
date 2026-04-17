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


class TestDenseAnnotationAreas:
    """Tests for dense annotation areas (P1 #5).

    Multiple annotations on the same paragraph may interfere during rendering.
    These tests verify that ordering and positions are preserved correctly.
    """

    def test_multiple_strokes_same_paragraph(self) -> None:
        """Test multiple strokes anchored to same paragraph."""
        content = "This is a single paragraph with multiple annotations."
        strokes = [
            _make_stroke("single", content, ocr_text="Note 1"),
            _make_stroke("paragraph", content, ocr_text="Note 2"),
            _make_stroke("annotations", content, ocr_text="Note 3"),
        ]

        model = DocumentModel(paragraphs=[], annotations=strokes, full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.strokes_rendered == 3
        # All footnote references should be present
        assert "[^stroke-1]" in result.content
        assert "[^stroke-2]" in result.content
        assert "[^stroke-3]" in result.content
        # All footnote definitions should be present
        assert "Note 1" in result.content
        assert "Note 2" in result.content
        assert "Note 3" in result.content

    def test_consecutive_highlights_no_space(self) -> None:
        """Test highlights on consecutive words (adjacent in text)."""
        content = "The quick brown fox jumps."
        # Highlight three consecutive words
        highlights = [
            _make_highlight("quick", content),
            _make_highlight("brown", content),
            _make_highlight("fox", content),
        ]

        model = DocumentModel(paragraphs=[], annotations=highlights, full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.highlights_rendered == 3
        # Each word should be independently highlighted
        assert "==quick==" in result.content
        assert "==brown==" in result.content
        assert "==fox==" in result.content
        # Verify they appear in order
        q_pos = result.content.find("==quick==")
        b_pos = result.content.find("==brown==")
        f_pos = result.content.find("==fox==")
        assert q_pos < b_pos < f_pos

    def test_overlapping_anchor_areas_strokes(self) -> None:
        """Test strokes with overlapping anchor text areas."""
        content = "Important text here needs multiple notes."
        # Multiple strokes anchored to overlapping text
        strokes = [
            _make_stroke("Important text", content, ocr_text="First note"),
            _make_stroke("text here", content, ocr_text="Second note"),
            _make_stroke("here needs", content, ocr_text="Third note"),
        ]

        model = DocumentModel(paragraphs=[], annotations=strokes, full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        # All strokes should render
        assert result.strokes_rendered == 3
        assert "First note" in result.content
        assert "Second note" in result.content
        assert "Third note" in result.content

    def test_mixed_dense_annotations(self) -> None:
        """Test dense area with both highlights and strokes."""
        content = "Critical data requires careful review and notes."
        highlight1 = _make_highlight("Critical", content)
        highlight2 = _make_highlight("data", content)
        stroke1 = _make_stroke("requires", content, ocr_text="Important!")
        stroke2 = _make_stroke("review", content, ocr_text="Check twice")

        model = DocumentModel(
            paragraphs=[],
            annotations=[highlight1, highlight2, stroke1, stroke2],
            full_text=content,
        )
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.highlights_rendered == 2
        assert result.strokes_rendered == 2
        assert "==Critical==" in result.content
        assert "==data==" in result.content
        assert "Important!" in result.content
        assert "Check twice" in result.content

    def test_annotation_ordering_preserved_on_same_word(self) -> None:
        """Test that multiple strokes on same anchor position are all rendered."""
        content = "The keyword here is important."
        # Two strokes on the same word
        strokes = [
            _make_stroke("keyword", content, ocr_text="First thought"),
            _make_stroke("keyword", content, ocr_text="Second thought"),
        ]

        model = DocumentModel(paragraphs=[], annotations=strokes, full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        # Both strokes should render, even on same word
        assert result.strokes_rendered == 2
        assert "First thought" in result.content
        assert "Second thought" in result.content

    def test_five_highlights_same_sentence(self) -> None:
        """Test 5 highlights in a single sentence (stress test)."""
        content = "The quick brown fox jumps over the lazy dog."
        highlights = [
            _make_highlight("The", content),
            _make_highlight("quick", content),
            _make_highlight("brown", content),
            _make_highlight("fox", content),
            _make_highlight("jumps", content),
        ]

        model = DocumentModel(paragraphs=[], annotations=highlights, full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.highlights_rendered == 5
        assert "==The==" in result.content
        assert "==quick==" in result.content
        assert "==brown==" in result.content
        assert "==fox==" in result.content
        assert "==jumps==" in result.content

    def test_five_strokes_same_paragraph(self) -> None:
        """Test 5 strokes in a single paragraph (stress test)."""
        content = "Paragraph with many words that all have annotations attached."
        strokes = [
            _make_stroke("Paragraph", content, ocr_text="Note A"),
            _make_stroke("many", content, ocr_text="Note B"),
            _make_stroke("words", content, ocr_text="Note C"),
            _make_stroke("annotations", content, ocr_text="Note D"),
            _make_stroke("attached", content, ocr_text="Note E"),
        ]

        model = DocumentModel(paragraphs=[], annotations=strokes, full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        assert result.strokes_rendered == 5
        for note in ["Note A", "Note B", "Note C", "Note D", "Note E"]:
            assert note in result.content

    def test_highlight_position_stability_after_multiple_insertions(self) -> None:
        """Verify highlight positions remain correct after inserting markers."""
        content = "Word1 Word2 Word3 Word4 Word5"
        # Create highlights in non-sequential order to test sorting
        highlights = [
            _make_highlight("Word3", content),
            _make_highlight("Word1", content),
            _make_highlight("Word5", content),
            _make_highlight("Word2", content),
            _make_highlight("Word4", content),
        ]

        model = DocumentModel(paragraphs=[], annotations=highlights, full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model)

        # All should be highlighted
        assert result.highlights_rendered == 5
        # Check relative order is correct in output
        expected = "==Word1== ==Word2== ==Word3== ==Word4== ==Word5=="
        assert expected in result.content


class TestOrphanCommentPlacementDynamicStructure:
    """P3 #13: Orphan comment placement with dynamic document structure.

    Documents the known limitation: the renderer only scans the first 100
    characters for an existing orphan comment. If the comment was moved below
    that boundary, a new comment is prepended and the old one remains.
    """

    def test_comment_at_top_is_detected_and_updated(self) -> None:
        """Orphan comment within first 100 chars is found and updated, not duplicated."""
        content = (
            "<!-- 1 orphaned annotation preserved in device file -->\n\n# Document\n\nContent."
        )

        assert "orphaned annotation" in content[:100]

        orphans = [_make_highlight("d1", "d1"), _make_highlight("d2", "d2")]
        model = DocumentModel(paragraphs=[], annotations=[], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model, orphaned_annotations=orphans)

        # Should be updated in-place, not duplicated
        assert result.content.count("orphaned annotation") == 1
        assert "2 orphaned annotations" in result.content

    def test_comment_at_bottom_is_detected_and_updated(self) -> None:
        """Orphan comment anywhere in the document is detected and updated in-place."""
        long_preamble = (
            "# Document\n\n"
            "This document has content that fills well over one hundred characters. "
            "The preamble is intentionally long to push the orphan comment past position 100.\n\n"
        )
        content = long_preamble + "<!-- 2 orphaned annotations preserved in device file -->\n\n"

        assert len(long_preamble) > 100
        assert "orphaned annotation" not in content[:100]  # Past the old 100-char boundary

        orphan = _make_highlight("deleted text", "deleted text context")
        model = DocumentModel(paragraphs=[], annotations=[], full_text=content)
        renderer = AnnotationRenderer()
        result = renderer.render(content, model, orphaned_annotations=[orphan])

        # Comment is found anywhere in the document and updated in-place (no duplicate)
        assert result.content.count("orphaned annotation") == 1
        assert "1 orphaned annotation" in result.content

    def test_comment_in_middle_after_user_adds_content_below(self) -> None:
        """Orphan comment in middle of document is updated in-place, not duplicated."""
        content = (
            "# Document\n\n"
            "Some content here with more than one hundred characters total in this block.\n\n"
            "<!-- 1 orphaned annotation preserved in device file -->\n\n"
            "User added this content below the comment afterward."
        )

        assert "orphaned annotation" not in content[:100]

        orphans = [
            _make_highlight("another deletion", "another deletion context"),
            _make_highlight("more deletion", "more deletion context"),
        ]
        model = DocumentModel(paragraphs=[], annotations=[], full_text=content)
        config = RenderConfig(orphan_comment_location="top")
        renderer = AnnotationRenderer(config)
        result = renderer.render(content, model, orphaned_annotations=orphans)

        # Comment is updated in-place to the new count — no duplicate
        assert result.content.count("orphaned annotation") == 1
        assert "2 orphaned annotations" in result.content


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
