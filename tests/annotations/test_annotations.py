"""
Comprehensive test suite for annotation extraction and preservation.

Tests use real .rm files from rmscene's test data to ensure compatibility
with actual reMarkable documents.
"""

from pathlib import Path

import pytest

from rock_paper_sync.annotations import (
    AnnotationType,
    Highlight,
    Point,
    Rectangle,
    Stroke,
    TextBlock,
    read_annotations,
)

# Path to rmscene test data
TESTDATA_DIR = Path(__file__).parent / "testdata" / "rmscene"


class TestReadAnnotations:
    """Tests for reading annotations from .rm files."""

    def test_read_strokes_from_file(self):
        """Test reading hand-drawn strokes from a real .rm file."""
        # Normal_A_stroke_2_layers.rm contains text "A" with 2 hand-drawn strokes
        file_path = TESTDATA_DIR / "Normal_A_stroke_2_layers.rm"
        annotations = read_annotations(file_path)

        # Filter to strokes only
        strokes = [a for a in annotations if a.type == AnnotationType.STROKE]

        assert len(strokes) == 2, "Should find 2 strokes in the test file"

        # Verify stroke structure
        for ann in strokes:
            assert ann.stroke is not None
            assert len(ann.stroke.points) > 0
            assert ann.stroke.color is not None
            assert ann.stroke.tool is not None
            assert ann.stroke.thickness > 0
            assert ann.stroke.bounding_box is not None

    def test_stroke_bounding_box_calculation(self):
        """Test that stroke bounding boxes are calculated correctly."""
        file_path = TESTDATA_DIR / "Normal_A_stroke_2_layers.rm"
        annotations = read_annotations(file_path)

        strokes = [a for a in annotations if a.type == AnnotationType.STROKE]
        assert len(strokes) > 0

        for ann in strokes:
            stroke = ann.stroke
            bbox = stroke.bounding_box

            # Bounding box should contain all points
            for point in stroke.points:
                assert (
                    bbox.x <= point.x <= bbox.x + bbox.w
                ), f"Point x={point.x} outside bbox x=[{bbox.x}, {bbox.x + bbox.w}]"
                assert (
                    bbox.y <= point.y <= bbox.y + bbox.h
                ), f"Point y={point.y} outside bbox y=[{bbox.y}, {bbox.y + bbox.h}]"

    def test_read_highlights_from_file(self):
        """Test reading text highlights from a real .rm file."""
        # Wikipedia_highlighted_p1.rm contains PDF with highlighted text
        file_path = TESTDATA_DIR / "Wikipedia_highlighted_p1.rm"
        annotations = read_annotations(file_path)

        # Filter to highlights only
        highlights = [a for a in annotations if a.type == AnnotationType.HIGHLIGHT]

        assert len(highlights) == 4, "Should find 4 highlights in the test file"

        # Verify highlight structure
        for ann in highlights:
            assert ann.highlight is not None
            assert len(ann.highlight.text) > 0
            assert ann.highlight.color is not None
            assert len(ann.highlight.rectangles) > 0

    def test_highlight_text_content(self):
        """Test that highlight text is extracted correctly."""
        file_path = TESTDATA_DIR / "Wikipedia_highlighted_p1.rm"
        annotations = read_annotations(file_path)

        highlights = [a for a in annotations if a.type == AnnotationType.HIGHLIGHT]

        # Check known highlight texts from Wikipedia test file
        expected_texts = [
            "The reMarkable uses electronic paper",
            "ReMarkable uses its own operating system, named Codex.",
            "Codex is based on Linux and optimized for electronic paper",
            "display technology.[13]",
        ]

        actual_texts = [h.highlight.text for h in highlights]

        assert (
            actual_texts == expected_texts
        ), f"Expected texts {expected_texts} but got {actual_texts}"

    def test_highlight_rectangles(self):
        """Test that highlight rectangles are extracted correctly."""
        file_path = TESTDATA_DIR / "Wikipedia_highlighted_p1.rm"
        annotations = read_annotations(file_path)

        highlights = [a for a in annotations if a.type == AnnotationType.HIGHLIGHT]

        for ann in highlights:
            for rect in ann.highlight.rectangles:
                # Rectangles should have positive dimensions
                assert rect.w > 0, "Rectangle width should be positive"
                assert rect.h > 0, "Rectangle height should be positive"

                # Verify center_y calculation
                expected_center = rect.y + rect.h / 2
                assert rect.center_y() == expected_center

    def test_empty_file_returns_empty_list(self):
        """Test that files without annotations return empty lists."""
        # Bold_Heading_Bullet_Normal.rm only has text, no annotations
        file_path = TESTDATA_DIR / "Bold_Heading_Bullet_Normal.rm"
        annotations = read_annotations(file_path)

        # Should have no strokes or highlights (only text)
        strokes = [a for a in annotations if a.type == AnnotationType.STROKE]
        highlights = [a for a in annotations if a.type == AnnotationType.HIGHLIGHT]

        assert len(strokes) == 0
        assert len(highlights) == 0

    def test_read_with_binary_file_object(self):
        """Test reading from an open file object instead of path."""
        file_path = TESTDATA_DIR / "Normal_A_stroke_2_layers.rm"

        with open(file_path, "rb") as f:
            annotations = read_annotations(f)

        strokes = [a for a in annotations if a.type == AnnotationType.STROKE]
        assert len(strokes) == 2


class TestAnnotationDataStructures:
    """Tests for annotation data structure helpers."""

    def test_stroke_center_y(self):
        """Test that stroke center_y returns the bounding box center."""
        points = [Point(0, 0), Point(10, 20), Point(5, 10)]
        stroke = Stroke(points=points, color=0, tool=1, thickness=1.0)

        # Bounding box: y=[0, 20], center should be 10
        assert stroke.center_y() == 10.0

    def test_highlight_center_y_single_rectangle(self):
        """Test highlight center_y with a single rectangle."""
        rect = Rectangle(x=0, y=100, w=200, h=50)
        highlight = Highlight(text="test", color=3, rectangles=[rect])

        # Center should be 100 + 50/2 = 125
        assert highlight.center_y() == 125.0

    def test_highlight_center_y_multiple_rectangles(self):
        """Test highlight center_y with multiple rectangles (spanning lines)."""
        rects = [
            Rectangle(x=0, y=100, w=200, h=50),  # center: 125
            Rectangle(x=0, y=150, w=100, h=50),  # center: 175
        ]
        highlight = Highlight(text="test", color=3, rectangles=rects)

        # Average center: (125 + 175) / 2 = 150
        assert highlight.center_y() == 150.0

    def test_rectangle_contains_point(self):
        """Test rectangle point containment check."""
        rect = Rectangle(x=10, y=20, w=30, h=40)

        # Inside points
        assert rect.contains_point(20, 30) is True
        assert rect.contains_point(10, 20) is True  # Edge
        assert rect.contains_point(40, 60) is True  # Edge

        # Outside points
        assert rect.contains_point(5, 30) is False
        assert rect.contains_point(50, 30) is False
        assert rect.contains_point(20, 10) is False
        assert rect.contains_point(20, 70) is False

    def test_text_block_contains_y(self):
        """Test text block Y coordinate containment."""
        block = TextBlock(content="Test paragraph", y_start=100, y_end=150, block_type="paragraph")

        assert block.contains_y(100) is True  # Start edge
        assert block.contains_y(125) is True  # Middle
        assert block.contains_y(150) is True  # End edge
        assert block.contains_y(99) is False  # Before
        assert block.contains_y(151) is False  # After


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
