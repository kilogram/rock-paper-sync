"""Round-trip tests for coordinate transformations.

These tests validate that coordinate transforms are reversible where applicable,
which is critical for bidirectional sync (M7).

Round-trip scenarios tested:
1. DocumentPoint ↔ PageLocalPoint (page-local storage)
2. DocumentPoint ↔ TextRelativePoint (text-relative storage)
3. Character offset ↔ Position (content anchoring)
4. Multi-page scenarios (cross-page transforms)
"""

import pytest

from rock_paper_sync.coordinates import (
    PAGE_CENTER_X,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    DocumentPoint,
    PageLayout,
    PageLocalPoint,
    TextOrigin,
)
from rock_paper_sync.layout import LayoutContext


class TestDocumentPageLocalRoundTrip:
    """Tests for DocumentPoint ↔ PageLocalPoint round-trips."""

    def test_page_0_simple(self):
        """Simple round-trip on page 0."""
        original = DocumentPoint(500.0, 300.0)
        local = original.to_page_local()
        roundtrip = local.to_document()

        assert roundtrip.x == original.x
        assert roundtrip.y == original.y

    def test_page_1_simple(self):
        """Simple round-trip on page 1."""
        original = DocumentPoint(500.0, PAGE_HEIGHT + 300.0)
        local = original.to_page_local()
        roundtrip = local.to_document()

        assert local.page == 1
        assert local.y == 300.0
        assert roundtrip.x == original.x
        assert abs(roundtrip.y - original.y) < 0.001

    def test_page_boundary_exact(self):
        """Round-trip at exact page boundary."""
        # Exactly at page 1 boundary
        original = DocumentPoint(500.0, PAGE_HEIGHT)
        local = original.to_page_local()
        roundtrip = local.to_document()

        assert local.page == 1
        assert local.y == 0.0
        assert roundtrip.x == original.x
        assert roundtrip.y == original.y

    def test_page_boundary_epsilon_below(self):
        """Round-trip just below page boundary."""
        original = DocumentPoint(500.0, PAGE_HEIGHT - 0.001)
        local = original.to_page_local()
        roundtrip = local.to_document()

        assert local.page == 0
        assert abs(local.y - (PAGE_HEIGHT - 0.001)) < 0.001
        assert abs(roundtrip.y - original.y) < 0.001

    def test_multi_page_deep(self):
        """Round-trip on page 5."""
        original = DocumentPoint(500.0, PAGE_HEIGHT * 5 + 500.0)
        local = original.to_page_local()
        roundtrip = local.to_document()

        assert local.page == 5
        assert local.y == 500.0
        assert abs(roundtrip.y - original.y) < 0.001

    def test_x_at_bounds(self):
        """Round-trip with X at page bounds."""
        # X at 0
        point_left = DocumentPoint(0.0, 500.0)
        local_left = point_left.to_page_local()
        rt_left = local_left.to_document()
        assert rt_left.x == 0.0

        # X at PAGE_WIDTH
        point_right = DocumentPoint(PAGE_WIDTH, 500.0)
        local_right = point_right.to_page_local()
        rt_right = local_right.to_document()
        assert rt_right.x == PAGE_WIDTH

    def test_non_uniform_layout(self):
        """Round-trip with non-uniform page heights."""
        layout = PageLayout(page_heights=(1000.0, 500.0, 1200.0), default_height=PAGE_HEIGHT)

        # Point on page 2 (starts at 1500)
        original = DocumentPoint(500.0, 1700.0)
        local = original.to_page_local(layout)
        roundtrip = local.to_document(layout)

        assert local.page == 2
        assert local.y == 200.0  # 1700 - 1500
        assert abs(roundtrip.y - original.y) < 0.001


class TestDocumentTextRelativeRoundTrip:
    """Tests for DocumentPoint ↔ TextRelativePoint round-trips."""

    @pytest.fixture
    def default_origin(self):
        return TextOrigin(x=-375.0, y=234.0)

    def test_at_text_origin(self, default_origin):
        """Round-trip at text area origin."""
        # Text origin in document space: (PAGE_CENTER_X + origin.x, origin.y)
        original = DocumentPoint(PAGE_CENTER_X - 375.0, 234.0)
        relative = original.to_text_relative(default_origin)
        roundtrip = relative.to_document(default_origin)

        assert relative.x == -375.0
        assert relative.y == 0.0
        assert roundtrip.x == original.x
        assert roundtrip.y == original.y

    def test_at_page_center(self, default_origin):
        """Round-trip at page center."""
        original = DocumentPoint(PAGE_CENTER_X, 500.0)
        relative = original.to_text_relative(default_origin)
        roundtrip = relative.to_document(default_origin)

        assert relative.x == 0.0  # At center
        assert relative.y == 500.0 - 234.0
        assert roundtrip.x == original.x
        assert roundtrip.y == original.y

    def test_right_of_center(self, default_origin):
        """Round-trip to right of page center."""
        original = DocumentPoint(PAGE_CENTER_X + 200.0, 400.0)
        relative = original.to_text_relative(default_origin)
        roundtrip = relative.to_document(default_origin)

        assert relative.x == 200.0
        assert roundtrip.x == original.x
        assert roundtrip.y == original.y

    def test_custom_origin(self):
        """Round-trip with custom text origin."""
        origin = TextOrigin(x=-400.0, y=100.0)
        original = DocumentPoint(500.0, 300.0)
        relative = original.to_text_relative(origin)
        roundtrip = relative.to_document(origin)

        assert roundtrip.x == original.x
        assert roundtrip.y == original.y

    def test_multiple_origins_same_point(self):
        """Same document point transforms differently with different origins."""
        origin1 = TextOrigin(x=-375.0, y=234.0)
        origin2 = TextOrigin(x=-300.0, y=200.0)
        doc_point = DocumentPoint(500.0, 400.0)

        rel1 = doc_point.to_text_relative(origin1)
        rel2 = doc_point.to_text_relative(origin2)

        # Should produce different relative coords
        assert rel1.x != rel2.x or rel1.y != rel2.y

        # But round-trip with matching origin should work
        rt1 = rel1.to_document(origin1)
        rt2 = rel2.to_document(origin2)

        assert rt1.x == doc_point.x
        assert rt1.y == doc_point.y
        assert rt2.x == doc_point.x
        assert rt2.y == doc_point.y


class TestOffsetPositionRoundTrip:
    """Tests for character offset ↔ position round-trips via LayoutContext.

    These tests are critical for bidirectional sync because:
    - Forward: offset_to_position is used when generating .rm files
    - Reverse: position_to_offset is used when reading annotations from device
    """

    @pytest.fixture
    def simple_context(self):
        """Layout context with simple text."""
        return LayoutContext.from_text(
            "Hello world\nLine two\nLine three",
            use_font_metrics=False,  # Predictable char widths
        )

    @pytest.fixture
    def multiline_context(self):
        """Layout context with multiple lines."""
        text = "\n".join([f"Line {i}: Some content here" for i in range(10)])
        return LayoutContext.from_text(text, use_font_metrics=False)

    @pytest.fixture
    def wrapped_context(self):
        """Layout context with word-wrapped content."""
        # Long line that will wrap
        text = "This is a very long line that will definitely need to wrap " * 5
        return LayoutContext.from_text(text, use_font_metrics=False)

    def test_first_character(self, simple_context):
        """Round-trip for first character."""
        original_offset = 0
        x, y = simple_context.offset_to_position(original_offset)
        roundtrip_offset = simple_context.position_to_offset(x, y)

        assert roundtrip_offset == original_offset

    def test_mid_line_character(self, simple_context):
        """Round-trip for character in middle of line."""
        original_offset = 6  # 'w' in "world"
        x, y = simple_context.offset_to_position(original_offset)
        roundtrip_offset = simple_context.position_to_offset(x, y)

        # May have small error due to font metrics approximation
        assert abs(roundtrip_offset - original_offset) <= 1

    def test_line_start(self, simple_context):
        """Round-trip for start of new line."""
        original_offset = 12  # Start of "Line two"
        x, y = simple_context.offset_to_position(original_offset)
        roundtrip_offset = simple_context.position_to_offset(x, y)

        assert abs(roundtrip_offset - original_offset) <= 1

    def test_multiple_lines(self, multiline_context):
        """Round-trip across multiple lines."""
        for line_num in range(5):
            # Find start of each line
            text = multiline_context.text_content
            lines = text.split("\n")
            offset = sum(len(lines[i]) + 1 for i in range(line_num))

            x, y = multiline_context.offset_to_position(offset)
            roundtrip = multiline_context.position_to_offset(x, y)

            # Should be within one character
            assert abs(roundtrip - offset) <= 1, f"Failed on line {line_num}"

    def test_wrapped_line(self, wrapped_context):
        """Round-trip on a wrapped line (same line, different visual rows)."""
        # Character somewhere in the middle of wrapped text
        original_offset = 100
        x, y = wrapped_context.offset_to_position(original_offset)
        roundtrip_offset = wrapped_context.position_to_offset(x, y)

        # May have larger error on wrapped lines
        assert abs(roundtrip_offset - original_offset) <= 2

    def test_end_of_text(self, simple_context):
        """Round-trip for last character."""
        text_len = len(simple_context.text_content)
        original_offset = text_len - 1

        x, y = simple_context.offset_to_position(original_offset)
        roundtrip_offset = simple_context.position_to_offset(x, y)

        assert abs(roundtrip_offset - original_offset) <= 1

    def test_y_determines_line(self, simple_context):
        """Verify Y position correctly determines line number."""
        # Get positions for characters on different lines
        x0, y0 = simple_context.offset_to_position(0)  # Line 1
        x1, y1 = simple_context.offset_to_position(12)  # Line 2
        x2, y2 = simple_context.offset_to_position(21)  # Line 3

        # Each line should have different Y
        assert y1 > y0
        assert y2 > y1

        # Round-trip should preserve line
        line0 = simple_context.get_line_for_y(y0)
        line1 = simple_context.get_line_for_y(y1)
        line2 = simple_context.get_line_for_y(y2)

        assert line0 == 0
        assert line1 == 1
        assert line2 == 2


class TestPositionToOffsetAccuracy:
    """Tests for position_to_offset accuracy (critical for reading annotations)."""

    def test_y_slightly_above_line(self):
        """Position slightly above a line should map to that line."""
        ctx = LayoutContext.from_text("Line one\nLine two\nLine three", use_font_metrics=False)

        # Get Y for line 2
        _, line2_y = ctx.offset_to_position(9)  # Start of "Line two"
        origin_x, _ = ctx.origin

        # Position slightly above line 2 start
        offset = ctx.position_to_offset(origin_x, line2_y - 5)

        # Should map to line 1 or early line 2
        assert 0 <= offset <= 15

    def test_y_slightly_below_line(self):
        """Position slightly below a line should map to that line."""
        ctx = LayoutContext.from_text("Line one\nLine two\nLine three", use_font_metrics=False)

        # Get Y for line 1
        _, line1_y = ctx.offset_to_position(0)
        origin_x, _ = ctx.origin

        # Position slightly below line 1
        offset = ctx.position_to_offset(origin_x, line1_y + 5)

        # Should map to line 1
        assert 0 <= offset < 9  # Line 1 content

    def test_x_left_of_text(self):
        """Position left of text should map to line start."""
        ctx = LayoutContext.from_text("Hello world", use_font_metrics=False)
        origin_x, origin_y = ctx.origin

        # Position to the left of text origin
        offset = ctx.position_to_offset(origin_x - 100, origin_y)

        assert offset == 0  # Should be at line start

    def test_x_right_of_text(self):
        """Position right of text should map to line end."""
        ctx = LayoutContext.from_text("Hello world", use_font_metrics=False)
        origin_x, origin_y = ctx.origin

        # Position far to the right of text
        offset = ctx.position_to_offset(origin_x + 1000, origin_y)

        # Should be at or near end of line
        assert offset >= 10  # Close to end of "Hello world"


class TestCombinedRoundTrips:
    """Tests combining multiple coordinate transforms."""

    def test_document_to_page_to_text_relative(self):
        """Chain of transforms should be consistent."""
        origin = TextOrigin(x=-375.0, y=234.0)
        original = DocumentPoint(500.0, PAGE_HEIGHT + 400.0)

        # Document -> Page-local -> Document -> Text-relative -> Document
        local = original.to_page_local()
        back_to_doc = local.to_document()
        text_rel = back_to_doc.to_text_relative(origin)
        final = text_rel.to_document(origin)

        assert abs(final.x - original.x) < 0.001
        assert abs(final.y - original.y) < 0.001

    def test_offset_to_position_cross_page(self):
        """Character offset to position with multi-page document."""
        # Create a long document that spans multiple pages
        lines = [f"Line {i}: Content content content" for i in range(100)]
        text = "\n".join(lines)
        ctx = LayoutContext.from_text(text, use_font_metrics=False)

        # Get position for character on different "pages" (by Y coordinate)
        offset_early = 50
        offset_late = 2000

        x1, y1 = ctx.offset_to_position(offset_early)
        x2, y2 = ctx.offset_to_position(offset_late)

        # Later offset should have larger Y
        assert y2 > y1

        # Round-trip should work
        rt1 = ctx.position_to_offset(x1, y1)
        rt2 = ctx.position_to_offset(x2, y2)

        assert abs(rt1 - offset_early) <= 1
        assert abs(rt2 - offset_late) <= 2


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_text(self):
        """Round-trip with empty text."""
        ctx = LayoutContext.from_text("", use_font_metrics=False)

        x, y = ctx.offset_to_position(0)
        offset = ctx.position_to_offset(x, y)

        assert offset == 0

    def test_single_character(self):
        """Round-trip with single character text."""
        ctx = LayoutContext.from_text("A", use_font_metrics=False)

        x, y = ctx.offset_to_position(0)
        offset = ctx.position_to_offset(x, y)

        assert offset == 0

    def test_only_newlines(self):
        """Round-trip with only newline characters."""
        ctx = LayoutContext.from_text("\n\n\n", use_font_metrics=False)

        # Each newline is a "line"
        x0, y0 = ctx.offset_to_position(0)
        x1, y1 = ctx.offset_to_position(1)
        x2, y2 = ctx.offset_to_position(2)

        # Different Y positions for each line
        assert y1 > y0
        assert y2 > y1

    def test_very_long_line(self):
        """Round-trip with very long line that wraps multiple times."""
        text = "A" * 500  # Long line
        ctx = LayoutContext.from_text(text, use_font_metrics=False)

        # Test at various points
        for offset in [0, 100, 250, 400, 499]:
            x, y = ctx.offset_to_position(offset)
            rt = ctx.position_to_offset(x, y)
            assert abs(rt - offset) <= 2, f"Failed at offset {offset}"

    def test_unicode_characters(self):
        """Round-trip with unicode characters."""
        text = "Hello \u4e16\u754c\nWorld \U0001f600"  # Chinese + emoji
        ctx = LayoutContext.from_text(text, use_font_metrics=False)

        # Should handle unicode without crashing
        for offset in range(min(10, len(text))):
            x, y = ctx.offset_to_position(offset)
            ctx.position_to_offset(x, y)  # Just verify no crash

    def test_page_local_negative_y(self):
        """PageLocalPoint with Y near 0."""
        local = PageLocalPoint(page=0, x=500.0, y=0.0)
        doc = local.to_document()
        roundtrip = doc.to_page_local()

        assert roundtrip.page == 0
        assert roundtrip.y == 0.0
