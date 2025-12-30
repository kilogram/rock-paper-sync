"""
Tests for AnchorContext resolution and layout engine.

Tests the AnchorContext anchor resolution system and word-wrap layout engine
for annotation position adjustment.
"""

import pytest

from rock_paper_sync.annotations import WordWrapLayoutEngine
from rock_paper_sync.annotations.document_model import AnchorContext


class TestAnchorContext:
    """Tests for AnchorContext resolution."""

    def test_create_anchor_from_text_span(self):
        """Test creating anchor from text span."""
        old_doc = "The quick brown fox jumps over the lazy dog."
        start = 10  # "brown fox"
        end = 19

        anchor = AnchorContext.from_text_span(old_doc, start, end, paragraph_index=0)

        assert anchor.text_content == "brown fox"
        assert anchor.paragraph_index == 0
        assert "The quick" in anchor.context_before
        assert "jumps" in anchor.context_after

    def test_resolve_anchor_exact_match(self):
        """Test resolving anchor with exact match."""
        old_doc = "The quick brown fox jumps over the lazy dog."
        new_doc = "A very quick brown fox leaps gracefully over the lazy dog."

        # Create anchor for "brown fox" in old doc
        anchor = AnchorContext.from_text_span(old_doc, 10, 19, paragraph_index=0)

        # Resolve in new document
        resolution = anchor.resolve(old_doc, new_doc, fuzzy_threshold=0.8)

        assert resolution is not None
        assert resolution.match_type == "exact"  # Content hash matches
        assert resolution.confidence >= 0.9
        # Verify the text at the resolved position
        assert new_doc[resolution.start_offset : resolution.end_offset] == "brown fox"

    def test_resolve_anchor_fuzzy_match(self):
        """Test resolving anchor with fuzzy match when content slightly changed."""
        old_doc = "The quick brown fox jumps."
        new_doc = "The quick brown foxx jumps."  # Typo in "fox"

        anchor = AnchorContext.from_text_span(old_doc, 10, 19, paragraph_index=0)
        resolution = anchor.resolve(old_doc, new_doc, fuzzy_threshold=0.7)

        # Should still resolve via fuzzy matching
        assert resolution is not None
        assert resolution.confidence >= 0.7

    def test_resolve_anchor_text_not_found(self):
        """Test resolving anchor when text was deleted."""
        old_doc = "The quick brown fox jumps."
        new_doc = "The quick dog runs."

        anchor = AnchorContext.from_text_span(old_doc, 10, 19, paragraph_index=0)
        resolution = anchor.resolve(old_doc, new_doc, fuzzy_threshold=0.8)

        # "brown fox" doesn't exist in new document
        assert resolution is None

    def test_resolve_anchor_with_context_disambiguation(self):
        """Test that context helps disambiguate multiple matches."""
        old_doc = "The cat is white. The fox jumps quickly."
        new_doc = "The fox jumps quickly today. The cat sleeps now."

        # Anchor "fox" with context "The" before and "jumps" after
        fox_start = old_doc.index("fox")
        anchor = AnchorContext.from_text_span(old_doc, fox_start, fox_start + 3, paragraph_index=0)

        resolution = anchor.resolve(old_doc, new_doc, fuzzy_threshold=0.8)

        # Should resolve successfully - exact match of "fox" with matching context
        assert resolution is not None
        assert new_doc[resolution.start_offset : resolution.end_offset] == "fox"

    def test_resolve_anchor_with_text_insertion(self):
        """Test resolving when text is inserted before the anchor."""
        old_doc = "Lorem ipsum dolor sit amet."
        new_doc = "INSERTED TEXT. Lorem ipsum dolor sit amet."

        anchor = AnchorContext.from_text_span(old_doc, 12, 21, paragraph_index=0)  # "dolor sit"
        resolution = anchor.resolve(old_doc, new_doc, fuzzy_threshold=0.8)

        assert resolution is not None
        # Text should be found after the insertion
        assert new_doc[resolution.start_offset : resolution.end_offset] == "dolor sit"
        assert resolution.start_offset > 12  # Shifted right


class TestWordWrapLayoutEngine:
    """Tests for WordWrapLayoutEngine."""

    def test_calculate_line_breaks_single_line(self):
        """Test line breaks for text that fits on one line."""
        engine = WordWrapLayoutEngine(text_width=750.0, avg_char_width=12.0, line_height=50.0)

        text = "Hello world"
        breaks = engine.calculate_line_breaks(text, 750.0)

        # Should have just one line starting at 0
        assert len(breaks) >= 1
        assert breaks[0] == 0

    def test_calculate_line_breaks_multiple_lines(self):
        """Test line breaks for text that wraps."""
        engine = WordWrapLayoutEngine(
            text_width=750.0,
            avg_char_width=12.0,  # ~62 chars per line
            line_height=50.0,
        )

        # Text with ~120 characters should wrap to 2 lines
        text = "This is a very long line of text that should definitely wrap to the next line when rendered on the device."
        breaks = engine.calculate_line_breaks(text, 750.0)

        # Should have multiple lines
        assert len(breaks) >= 2

    def test_calculate_line_breaks_explicit_newlines(self):
        """Test line breaks with explicit newlines."""
        engine = WordWrapLayoutEngine()

        text = "Line 1\nLine 2\nLine 3"
        breaks = engine.calculate_line_breaks(text, 750.0)

        # Should have at least 3 lines (one for each explicit newline)
        assert len(breaks) >= 3

    def test_offset_to_position_first_line(self):
        """Test converting offset to position on first line."""
        engine = WordWrapLayoutEngine(text_width=750.0, avg_char_width=12.0, line_height=50.0)

        text = "Hello world"
        origin = (-375.0, 94.0)

        # Offset 6 is position of "world"
        x, y = engine.offset_to_position(6, text, origin, 750.0)

        # Should be on first line (y=94) at x position 6*12 from origin
        assert y == 94.0
        assert x == -375.0 + (6 * 12.0)

    def test_offset_to_position_second_line(self):
        """Test converting offset to position on second line."""
        engine = WordWrapLayoutEngine(text_width=750.0, avg_char_width=12.0, line_height=50.0)

        text = "Line 1\nLine 2"
        origin = (-375.0, 94.0)

        # Offset 7 is start of "Line 2" (after "Line 1\n")
        x, y = engine.offset_to_position(7, text, origin, 750.0)

        # Should be on second line (y=94+50=144)
        assert y == 94.0 + 50.0

    def test_offset_to_position_wrapped_line(self):
        """Test position calculation with word wrapping."""
        engine = WordWrapLayoutEngine(
            text_width=750.0,
            avg_char_width=12.0,  # ~62 chars per line
            line_height=50.0,
        )

        # Create text that will wrap
        text = "a " * 100  # 200 characters, should wrap to multiple lines
        origin = (-375.0, 94.0)

        # Offset 150 should be on a later line
        x, y = engine.offset_to_position(150, text, origin, 750.0)

        # Y should be greater than origin (wrapped to another line)
        assert y > 94.0

    def test_get_line_height(self):
        """Test getting line height."""
        engine = WordWrapLayoutEngine(line_height=42.0)
        assert engine.get_line_height() == 42.0

    def test_get_avg_char_width(self):
        """Test getting average character width."""
        engine = WordWrapLayoutEngine(avg_char_width=15.0)
        assert engine.get_avg_char_width() == 15.0


class TestHighlightRectangleCalculation:
    """Tests for calculate_highlight_rectangles method."""

    def test_single_line_highlight(self):
        """Test rectangle calculation for single-line highlight."""
        engine = WordWrapLayoutEngine(text_width=750.0, avg_char_width=15.0, line_height=35.0)

        text = "The target word is here."
        origin = (-375.0, 94.0)

        # Highlight "target" which starts at offset 4
        rects = engine.calculate_highlight_rectangles(
            start_offset=4,
            end_offset=10,
            text=text,
            origin=origin,
            width=750.0,
        )

        assert len(rects) == 1
        x, y, w, h = rects[0]
        assert x == origin[0] + 4 * 15.0  # 4 chars before
        assert y == origin[1]  # First line
        assert w == 6 * 15.0  # 6 chars wide
        assert h == 35.0  # Default line height

    def test_highlight_x_shift_after_insert(self):
        """Test that X position shifts when text is inserted before highlight."""
        engine = WordWrapLayoutEngine(text_width=750.0, avg_char_width=15.0, line_height=35.0)

        old_text = "The target word is here."
        new_text = "The INSERTED target word is here."
        origin = (-375.0, 94.0)

        # Create anchor for "target" in old text
        old_start = old_text.index("target")
        anchor = AnchorContext.from_text_span(old_text, old_start, old_start + 6, paragraph_index=0)

        # Resolve in new text
        resolution = anchor.resolve(old_text, new_text, fuzzy_threshold=0.8)
        assert resolution is not None

        old_rects = engine.calculate_highlight_rectangles(
            old_start, old_start + 6, old_text, origin, 750.0
        )
        new_rects = engine.calculate_highlight_rectangles(
            resolution.start_offset, resolution.end_offset, new_text, origin, 750.0
        )

        assert len(old_rects) == 1
        assert len(new_rects) == 1

        x_delta = new_rects[0][0] - old_rects[0][0]
        # "INSERTED " is 9 chars * 15px = 135px
        assert x_delta > 100, f"Expected x_delta > 100, got {x_delta}"

    def test_multiline_highlight(self):
        """Test rectangle calculation spanning multiple lines."""
        engine = WordWrapLayoutEngine(text_width=150.0, avg_char_width=15.0, line_height=35.0)
        # 150px / 15px = 10 chars per line

        text = "Short words wrap to multiple lines here."
        origin = (0.0, 0.0)

        # Highlight from offset 6 to 25 (should span multiple lines)
        rects = engine.calculate_highlight_rectangles(
            start_offset=6,
            end_offset=25,
            text=text,
            origin=origin,
            width=150.0,
        )

        # Should have multiple rectangles (one per line)
        assert len(rects) >= 1

    def test_custom_rect_height(self):
        """Test custom rectangle height parameter."""
        engine = WordWrapLayoutEngine(text_width=750.0, avg_char_width=15.0, line_height=35.0)

        text = "Hello world"
        origin = (0.0, 0.0)

        rects = engine.calculate_highlight_rectangles(
            start_offset=0,
            end_offset=5,
            text=text,
            origin=origin,
            width=750.0,
            rect_height=20.0,
        )

        assert len(rects) == 1
        assert rects[0][3] == 20.0  # Custom height


class TestIntegration:
    """Integration tests for text anchoring with layout engine."""

    def test_highlight_adjustment_after_text_insert(self):
        """Test that highlight position is adjusted when text is inserted before it."""
        layout_engine = WordWrapLayoutEngine(avg_char_width=12.0, line_height=50.0)

        old_text = "Hello world, this is a test."
        new_text = "INSERTED TEXT HERE. Hello world, this is a test."

        # Highlight "world" in old text
        old_start = old_text.index("world")
        old_origin = (-375.0, 94.0)
        new_origin = (-375.0, 94.0)

        # Create anchor
        anchor = AnchorContext.from_text_span(old_text, old_start, old_start + 5, paragraph_index=0)

        # Resolve in new text
        resolution = anchor.resolve(old_text, new_text, fuzzy_threshold=0.8)
        assert resolution is not None
        assert resolution.start_offset == 26  # Shifted right by length of inserted text + space

        # Calculate new position
        new_x, new_y = layout_engine.offset_to_position(
            resolution.start_offset, new_text, new_origin, 750.0
        )

        # X should have moved right
        old_x, old_y = layout_engine.offset_to_position(old_start, old_text, old_origin, 750.0)

        assert new_x > old_x  # Moved right due to inserted text

    def test_highlight_adjustment_after_text_wrap(self):
        """Test highlight adjustment when text wraps to new line."""
        layout_engine = WordWrapLayoutEngine(
            text_width=750.0,
            avg_char_width=12.0,  # ~62 chars per line
            line_height=50.0,
        )

        # Short text, highlight on first line
        old_text = "The quick brown fox"
        old_origin = (-375.0, 94.0)

        # Long text causes wrapping, "fox" moves to second line
        new_text = "A very very very long prefix that causes wrapping here. The quick brown fox"
        new_origin = (-375.0, 94.0)

        # Create anchor for "fox"
        old_start = old_text.index("fox")
        anchor = AnchorContext.from_text_span(old_text, old_start, old_start + 3, paragraph_index=0)

        # Resolve in new text
        resolution = anchor.resolve(old_text, new_text, fuzzy_threshold=0.8)
        assert resolution is not None

        # Calculate positions
        old_x, old_y = layout_engine.offset_to_position(old_start, old_text, old_origin, 750.0)
        new_x, new_y = layout_engine.offset_to_position(
            resolution.start_offset, new_text, new_origin, 750.0
        )

        # Y should have increased (moved to different line potentially)
        # This depends on wrapping behavior
        assert resolution.start_offset > old_start  # Moved right in document

    def test_highlight_stays_with_text_after_paragraph_addition(self):
        """Test that highlights stay anchored when paragraphs are added."""
        old_text = "First paragraph.\n\nSecond paragraph with important text."
        new_text = (
            "First paragraph.\n\nNEW PARAGRAPH ADDED.\n\nSecond paragraph with important text."
        )

        # Create anchor for "important" in second paragraph
        old_start = old_text.index("important")
        anchor = AnchorContext.from_text_span(
            old_text, old_start, old_start + 9, paragraph_index=0, y_position=200.0
        )

        # Resolve in new text
        resolution = anchor.resolve(old_text, new_text, fuzzy_threshold=0.8)

        # Should still find "important"
        assert resolution is not None
        assert new_text[resolution.start_offset : resolution.end_offset] == "important"

    def test_low_confidence_anchor_handling(self):
        """Test handling of anchors that can't be resolved."""
        old_text = "The quick brown fox"
        new_text = "A completely different text"

        # Create anchor for "fox"
        old_start = old_text.index("fox")
        anchor = AnchorContext.from_text_span(old_text, old_start, old_start + 3, paragraph_index=0)

        # Try to resolve in completely different text with high threshold
        resolution = anchor.resolve(old_text, new_text, fuzzy_threshold=0.9)

        # Should not find it
        assert resolution is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
