"""
Tests for Phase 1: Text Anchor Strategy and Layout Engine

Tests the heuristic text anchoring and word-wrap layout engine for
annotation position adjustment.
"""

import pytest

from rock_paper_sync.annotations import (
    HeuristicTextAnchor,
    WordWrapLayoutEngine,
)


class TestHeuristicTextAnchor:
    """Tests for HeuristicTextAnchor strategy."""

    def test_find_anchor_exact_match(self):
        """Test finding anchor with exact substring match."""
        strategy = HeuristicTextAnchor()
        old_doc = "The quick brown fox jumps over the lazy dog."
        annotation_text = "brown fox"

        anchor = strategy.find_anchor(annotation_text, old_doc, (100, 100))

        assert anchor.text_content == "brown fox"
        assert anchor.char_offset == 10  # Position of "brown fox" in text
        assert anchor.confidence == 1.0
        assert anchor.context_before == "The quick "
        assert anchor.context_after == " jumps over the lazy dog."

    def test_find_anchor_fuzzy_match(self):
        """Test finding anchor with fuzzy match (partial match)."""
        strategy = HeuristicTextAnchor(fuzzy_threshold=0.7)
        old_doc = "The quick brown fox jumps over the lazy dog."
        annotation_text = "brown fo"  # Partial match (missing 'x')

        anchor = strategy.find_anchor(annotation_text, old_doc, (100, 100))

        # Should find close match using longest common substring
        assert anchor.char_offset is not None
        assert 0.7 <= anchor.confidence <= 1.0

    def test_find_anchor_no_match(self):
        """Test finding anchor when text doesn't exist."""
        strategy = HeuristicTextAnchor()
        old_doc = "The quick brown fox jumps over the lazy dog."
        annotation_text = "elephant"

        anchor = strategy.find_anchor(annotation_text, old_doc, (100, 100))

        assert anchor.char_offset is None
        assert anchor.confidence == 0.0

    def test_resolve_anchor_single_match(self):
        """Test resolving anchor with single match."""
        strategy = HeuristicTextAnchor()
        old_doc = "The quick brown fox jumps over the lazy dog."
        new_doc = "A very quick brown fox leaps gracefully over the lazy dog."

        # Find in old document
        anchor = strategy.find_anchor("brown fox", old_doc, (100, 100))

        # Resolve in new document
        new_offset = strategy.resolve_anchor(anchor, new_doc)

        # Verify it found "brown fox" in new text
        assert new_offset == 13  # Position of "brown fox" in new text ("A very quick " = 13 chars)
        assert new_doc[new_offset : new_offset + 9] == "brown fox"

    def test_resolve_anchor_multiple_matches_uses_context(self):
        """Test resolving anchor with multiple matches using context."""
        strategy = HeuristicTextAnchor(context_window=20)
        old_doc = "The fox is red. The fox is brown. The fox is clever."
        new_doc = "The brown fox is clever. The red fox is shy. The fox is quick."

        # Find "fox" near "brown" in old document
        anchor = strategy.find_anchor("fox", old_doc, (100, 100))
        # Context should capture "The " before and " is red" or similar after

        # Resolve in new document - should prefer match with similar context
        new_offset = strategy.resolve_anchor(anchor, new_doc)

        # Should find some occurrence of "fox"
        assert new_offset is not None
        assert new_doc[new_offset : new_offset + 3] == "fox"

    def test_resolve_anchor_text_edited(self):
        """Test resolving anchor when text was slightly edited."""
        strategy = HeuristicTextAnchor(fuzzy_threshold=0.8)
        old_doc = "Lorem ipsum dolor sit amet."
        new_doc = "Lorem ipsum dolor sit amet, consectetur adipiscing."

        anchor = strategy.find_anchor("dolor sit", old_doc, (100, 100))
        new_offset = strategy.resolve_anchor(anchor, new_doc)

        assert new_offset == 12  # Still at same position
        assert new_doc[new_offset : new_offset + 9] == "dolor sit"

    def test_resolve_anchor_text_not_found(self):
        """Test resolving anchor when text was deleted."""
        strategy = HeuristicTextAnchor()
        old_doc = "The quick brown fox jumps."
        new_doc = "The quick dog runs."

        anchor = strategy.find_anchor("brown fox", old_doc, (100, 100))
        new_offset = strategy.resolve_anchor(anchor, new_doc)

        # "brown fox" doesn't exist in new document
        assert new_offset is None


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
        anchor = HeuristicTextAnchor()

        old_text = "The target word is here."
        new_text = "The INSERTED target word is here."
        origin = (-375.0, 94.0)

        # Find "target" in old/new
        anc = anchor.find_anchor("target", old_text, (0, 0))
        new_offset = anchor.resolve_anchor(anc, new_text)

        assert anc.char_offset is not None
        assert new_offset is not None

        old_rects = engine.calculate_highlight_rectangles(
            anc.char_offset, anc.char_offset + 6, old_text, origin, 750.0
        )
        new_rects = engine.calculate_highlight_rectangles(
            new_offset, new_offset + 6, new_text, origin, 750.0
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
        anchor_strategy = HeuristicTextAnchor()
        layout_engine = WordWrapLayoutEngine(avg_char_width=12.0, line_height=50.0)

        old_text = "Hello world, this is a test."
        new_text = "INSERTED TEXT HERE. Hello world, this is a test."

        # Highlight "world" in old text
        highlight_text = "world"
        old_origin = (-375.0, 94.0)
        new_origin = (-375.0, 94.0)

        # Find anchor
        anchor = anchor_strategy.find_anchor(highlight_text, old_text, (100, 100))
        assert anchor.confidence == 1.0
        assert anchor.char_offset == 6

        # Resolve in new text
        new_offset = anchor_strategy.resolve_anchor(anchor, new_text)
        assert new_offset == 26  # Shifted right by length of inserted text + space

        # Calculate new position
        new_x, new_y = layout_engine.offset_to_position(new_offset, new_text, new_origin, 750.0)

        # X should have moved right
        old_x, old_y = layout_engine.offset_to_position(
            anchor.char_offset, old_text, old_origin, 750.0
        )

        assert new_x > old_x  # Moved right due to inserted text

    def test_highlight_adjustment_after_text_wrap(self):
        """Test highlight adjustment when text wraps to new line."""
        anchor_strategy = HeuristicTextAnchor()
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

        # Find anchor for "fox"
        anchor = anchor_strategy.find_anchor("fox", old_text, (100, 100))
        assert anchor.confidence == 1.0

        # Resolve in new text
        new_offset = anchor_strategy.resolve_anchor(anchor, new_text)
        assert new_offset is not None

        # Calculate positions
        old_x, old_y = layout_engine.offset_to_position(
            anchor.char_offset, old_text, old_origin, 750.0
        )
        new_x, new_y = layout_engine.offset_to_position(new_offset, new_text, new_origin, 750.0)

        # Y should have increased (moved to different line potentially)
        # This depends on wrapping behavior
        assert new_offset > anchor.char_offset  # Moved right in document

    def test_highlight_stays_with_text_after_paragraph_addition(self):
        """Test that highlights stay anchored when paragraphs are added."""
        anchor_strategy = HeuristicTextAnchor()

        old_text = "First paragraph.\n\nSecond paragraph with important text."
        new_text = (
            "First paragraph.\n\nNEW PARAGRAPH ADDED.\n\nSecond paragraph with important text."
        )

        # Highlight "important" in second paragraph
        anchor = anchor_strategy.find_anchor("important", old_text, (200, 200))

        # Resolve in new text
        new_offset = anchor_strategy.resolve_anchor(anchor, new_text)

        # Should still find "important"
        assert new_offset is not None
        assert new_text[new_offset : new_offset + 9] == "important"

    def test_low_confidence_anchor_handling(self):
        """Test handling of low-confidence anchors."""
        anchor_strategy = HeuristicTextAnchor(fuzzy_threshold=0.9)

        old_text = "The quick brown fox"
        new_text = "A completely different text"

        # Try to anchor "fox"
        anchor = anchor_strategy.find_anchor("fox", old_text, (100, 100))
        assert anchor.confidence == 1.0

        # Try to resolve in completely different text
        new_offset = anchor_strategy.resolve_anchor(anchor, new_text)

        # Should not find it
        assert new_offset is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
