"""Tests for the transform module - pure coordinate transformation utilities."""

from rock_paper_sync.transform import (
    Position,
    PositionDelta,
    Rectangle,
    TextSpan,
    apply_delta_to_rectangles,
    find_all_occurrences,
    merge_adjacent_rectangles,
    rebuild_for_reflow,
    rectangles_from_tuples,
    rectangles_to_tuples,
    resolve_anchor,
    resolve_by_relative_position,
)


class TestPosition:
    """Tests for Position type."""

    def test_offset_by(self):
        """Position offset_by applies delta correctly."""
        pos = Position(100.0, 200.0)
        delta = PositionDelta(10.0, -20.0)

        result = pos.offset_by(delta)

        assert result.x == 110.0
        assert result.y == 180.0

    def test_distance_to(self):
        """Position distance_to calculates Euclidean distance."""
        p1 = Position(0.0, 0.0)
        p2 = Position(3.0, 4.0)

        assert p1.distance_to(p2) == 5.0

    def test_unpack(self):
        """Position can be unpacked as tuple."""
        pos = Position(100.0, 200.0)
        x, y = pos
        assert x == 100.0
        assert y == 200.0


class TestPositionDelta:
    """Tests for PositionDelta type."""

    def test_between(self):
        """PositionDelta.between calculates delta correctly."""
        old = Position(100.0, 200.0)
        new = Position(150.0, 250.0)

        delta = PositionDelta.between(old, new)

        assert delta.dx == 50.0
        assert delta.dy == 50.0

    def test_zero(self):
        """PositionDelta.zero creates zero delta."""
        delta = PositionDelta.zero()
        assert delta.dx == 0.0
        assert delta.dy == 0.0

    def test_magnitude(self):
        """PositionDelta.magnitude calculates length."""
        delta = PositionDelta(3.0, 4.0)
        assert delta.magnitude == 5.0

    def test_add(self):
        """PositionDeltas can be added."""
        d1 = PositionDelta(10.0, 20.0)
        d2 = PositionDelta(5.0, -10.0)

        result = d1 + d2

        assert result.dx == 15.0
        assert result.dy == 10.0


class TestRectangle:
    """Tests for Rectangle type."""

    def test_center(self):
        """Rectangle.center calculates center correctly."""
        rect = Rectangle(100.0, 200.0, 50.0, 30.0)
        center = rect.center
        assert center.x == 125.0
        assert center.y == 215.0

    def test_offset_by(self):
        """Rectangle.offset_by applies delta correctly."""
        rect = Rectangle(100.0, 200.0, 50.0, 30.0)
        delta = PositionDelta(10.0, -20.0)

        result = rect.offset_by(delta)

        assert result.x == 110.0
        assert result.y == 180.0
        assert result.width == 50.0  # Unchanged
        assert result.height == 30.0  # Unchanged

    def test_contains(self):
        """Rectangle.contains checks point containment."""
        rect = Rectangle(100.0, 200.0, 50.0, 30.0)

        assert rect.contains(Position(125.0, 215.0))  # Center
        assert rect.contains(Position(100.0, 200.0))  # Top-left
        assert not rect.contains(Position(99.0, 200.0))  # Just outside

    def test_intersects(self):
        """Rectangle.intersects checks overlap."""
        r1 = Rectangle(0.0, 0.0, 100.0, 100.0)
        r2 = Rectangle(50.0, 50.0, 100.0, 100.0)  # Overlaps
        r3 = Rectangle(200.0, 200.0, 100.0, 100.0)  # No overlap

        assert r1.intersects(r2)
        assert not r1.intersects(r3)

    def test_from_tuple_to_tuple(self):
        """Rectangle can round-trip through tuples."""
        original = (100.0, 200.0, 50.0, 30.0)
        rect = Rectangle.from_tuple(original)
        result = rect.to_tuple()
        assert result == original


class TestTextSpan:
    """Tests for TextSpan type."""

    def test_length(self):
        """TextSpan.length calculates correctly."""
        span = TextSpan(10, 25)
        assert span.length == 15

    def test_offset_by(self):
        """TextSpan.offset_by shifts span correctly."""
        span = TextSpan(10, 25)
        result = span.offset_by(5)
        assert result.start == 15
        assert result.end == 30

    def test_contains_offset(self):
        """TextSpan.contains_offset checks containment."""
        span = TextSpan(10, 25)
        assert span.contains_offset(10)
        assert span.contains_offset(20)
        assert not span.contains_offset(25)  # End is exclusive
        assert not span.contains_offset(9)

    def test_extract_from(self):
        """TextSpan.extract_from gets text content."""
        span = TextSpan(6, 11)
        text = "Hello World!"
        assert span.extract_from(text) == "World"


class TestApplyDeltaToRectangles:
    """Tests for apply_delta_to_rectangles function."""

    def test_applies_delta_to_all(self):
        """Delta is applied to all rectangles."""
        rects = [
            Rectangle(100.0, 200.0, 50.0, 20.0),
            Rectangle(100.0, 220.0, 80.0, 20.0),
        ]
        delta = PositionDelta(0.0, 57.0)

        result = apply_delta_to_rectangles(rects, delta)

        assert len(result) == 2
        assert result[0].y == 257.0
        assert result[1].y == 277.0
        # Width/height unchanged
        assert result[0].width == 50.0
        assert result[1].height == 20.0

    def test_empty_list(self):
        """Empty list returns empty list."""
        result = apply_delta_to_rectangles([], PositionDelta(10.0, 20.0))
        assert result == []


class TestRebuildForReflow:
    """Tests for rebuild_for_reflow function."""

    def test_preserves_first_rect_with_delta(self):
        """First rectangle uses delta, not layout position."""
        original_first = Rectangle(105.0, 200.0, 50.0, 22.0)  # Device-captured
        layout_rects = [
            (100.0, 300.0, 48.0, 20.0),  # Layout approximation
            (100.0, 320.0, 100.0, 20.0),  # New line from reflow
        ]
        delta = PositionDelta(0.0, 100.0)

        result = rebuild_for_reflow(
            original_first_rect=original_first,
            layout_rects=layout_rects,
            delta=delta,
            text_origin_x=100.0,
        )

        assert len(result) == 2
        # First rect: original position + delta
        assert result[0].x == 105.0  # Preserved from device
        assert result[0].y == 300.0  # 200 + 100 delta
        assert result[0].height == 22.0  # Original height preserved
        # Second rect uses geometry
        assert result[1].x == 100.0  # Line start
        assert result[1].y == 322.0  # First rect y + original height

    def test_empty_layout_rects(self):
        """Empty layout rects returns empty list."""
        result = rebuild_for_reflow(
            original_first_rect=Rectangle(100.0, 200.0, 50.0, 20.0),
            layout_rects=[],
            delta=PositionDelta(0.0, 0.0),
            text_origin_x=100.0,
        )
        assert result == []


class TestResolveAnchor:
    """Tests for resolve_anchor function."""

    def test_exact_match_same_position(self):
        """Exact match at same position gets highest confidence."""
        old_text = "Hello world"
        new_text = "Hello world"

        result = resolve_anchor(
            anchor_text="world",
            old_offset=6,
            old_text=old_text,
            new_text=new_text,
        )

        assert result is not None
        assert result.new_offset == 6
        assert result.confidence == 1.0
        assert result.match_type == "exact"

    def test_exact_match_shifted(self):
        """Exact match after insertion finds shifted position."""
        old_text = "Hello world"
        new_text = "Greeting: Hello world"  # 10 chars inserted

        result = resolve_anchor(
            anchor_text="world",
            old_offset=6,
            old_text=old_text,
            new_text=new_text,
        )

        assert result is not None
        assert result.new_offset == 16  # 6 + 10
        assert result.confidence >= 0.5
        assert result.match_type == "exact_nearby"

    def test_context_match(self):
        """Context matching finds anchor using surrounding text."""
        old_text = "The important concept is explained here."
        new_text = "First paragraph.\n\nThe important concept is explained here."

        result = resolve_anchor(
            anchor_text="concept",
            old_offset=14,
            old_text=old_text,
            new_text=new_text,
            context_before="important ",
            context_after=" is",
        )

        assert result is not None
        assert new_text[result.new_offset : result.new_offset + 7] == "concept"

    def test_not_found(self):
        """Returns None when anchor cannot be found."""
        result = resolve_anchor(
            anchor_text="nonexistent",
            old_offset=0,
            old_text="Hello world",
            new_text="Goodbye world",
        )

        assert result is None


class TestResolveByRelativePosition:
    """Tests for resolve_by_relative_position function."""

    def test_maintains_relative_position(self):
        """Fallback maintains relative document position."""
        old_text = "Hello world"  # 11 chars
        new_text = "Hello beautiful world"  # 21 chars

        result = resolve_by_relative_position(
            old_offset=6,  # ~55% through
            old_text=old_text,
            new_text=new_text,
        )

        # Should be roughly 55% through new text
        assert result.new_offset == 11  # int(0.545 * 21)
        assert result.confidence == 0.3  # Low confidence fallback
        assert result.match_type == "fallback"


class TestFindAllOccurrences:
    """Tests for find_all_occurrences function."""

    def test_finds_all(self):
        """Finds all occurrences of pattern."""
        text = "the cat sat on the mat near the hat"
        result = find_all_occurrences(text, "the")
        assert result == [0, 15, 28]

    def test_no_occurrences(self):
        """Returns empty list when pattern not found."""
        result = find_all_occurrences("hello world", "xyz")
        assert result == []


class TestMergeAdjacentRectangles:
    """Tests for merge_adjacent_rectangles function."""

    def test_merges_same_line(self):
        """Rectangles on same line are merged."""
        rects = [
            Rectangle(100.0, 200.0, 30.0, 20.0),
            Rectangle(140.0, 200.0, 40.0, 20.0),  # Same Y
        ]

        result = merge_adjacent_rectangles(rects)

        assert len(result) == 1
        assert result[0].x == 100.0
        assert result[0].width == 80.0  # Spans both

    def test_keeps_different_lines(self):
        """Rectangles on different lines are kept separate."""
        rects = [
            Rectangle(100.0, 200.0, 50.0, 20.0),
            Rectangle(100.0, 250.0, 50.0, 20.0),  # Different Y
        ]

        result = merge_adjacent_rectangles(rects)

        assert len(result) == 2


class TestRectanglesConversion:
    """Tests for tuple conversion utilities."""

    def test_from_tuples(self):
        """rectangles_from_tuples converts correctly."""
        tuples = [(100.0, 200.0, 50.0, 20.0), (100.0, 220.0, 80.0, 20.0)]
        result = rectangles_from_tuples(tuples)

        assert len(result) == 2
        assert isinstance(result[0], Rectangle)
        assert result[0].x == 100.0

    def test_to_tuples(self):
        """rectangles_to_tuples converts correctly."""
        rects = [
            Rectangle(100.0, 200.0, 50.0, 20.0),
            Rectangle(100.0, 220.0, 80.0, 20.0),
        ]
        result = rectangles_to_tuples(rects)

        assert result == [(100.0, 200.0, 50.0, 20.0), (100.0, 220.0, 80.0, 20.0)]
