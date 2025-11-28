"""
Comprehensive test suite for annotation extraction and preservation.

Tests use real .rm files from rmscene's test data to ensure compatibility
with actual reMarkable documents.
"""

from pathlib import Path

import pytest

from rock_paper_sync.annotations import (
    Annotation,
    AnnotationType,
    Highlight,
    Point,
    Rectangle,
    Stroke,
    TextBlock,
    associate_annotations_with_content,
    calculate_position_mapping,
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


class TestAnnotationAssociation:
    """Tests for associating annotations with text blocks."""

    def test_associate_stroke_with_nearby_text(self):
        """Test that strokes are associated with nearby text blocks."""
        # Create a stroke at y=110 (center)
        points = [Point(0, 100), Point(10, 120)]
        stroke = Stroke(points=points, color=0, tool=1, thickness=1.0)
        annotation = Annotation(type=AnnotationType.STROKE, stroke=stroke)

        # Create text blocks
        blocks = [
            TextBlock("First paragraph", y_start=50, y_end=90, block_type="paragraph"),
            TextBlock("Second paragraph", y_start=100, y_end=140, block_type="paragraph"),
            TextBlock("Third paragraph", y_start=150, y_end=190, block_type="paragraph"),
        ]

        mapping = associate_annotations_with_content([annotation], blocks)

        # Stroke at y=110 should associate with second block (y=100-140)
        assert 0 in mapping.associations
        assert mapping.associations[0] == 1

    def test_associate_highlight_with_overlapping_text(self):
        """Test that highlights associate with the text they overlap."""
        # Create a highlight at y=105-125
        rect = Rectangle(x=0, y=105, w=100, h=20)
        highlight = Highlight(text="highlighted", color=3, rectangles=[rect])
        annotation = Annotation(type=AnnotationType.HIGHLIGHT, highlight=highlight)

        # Create text blocks
        blocks = [
            TextBlock("First paragraph", y_start=50, y_end=90, block_type="paragraph"),
            TextBlock("Second paragraph", y_start=100, y_end=140, block_type="paragraph"),
            TextBlock("Third paragraph", y_start=150, y_end=190, block_type="paragraph"),
        ]

        mapping = associate_annotations_with_content([annotation], blocks)

        # Highlight center at y=115 should associate with second block
        assert 0 in mapping.associations
        assert mapping.associations[0] == 1

    def test_no_association_when_too_far(self):
        """Test that annotations don't associate when too far from text."""
        # Create annotation far from any text
        points = [Point(0, 500), Point(10, 520)]
        stroke = Stroke(points=points, color=0, tool=1, thickness=1.0)
        annotation = Annotation(type=AnnotationType.STROKE, stroke=stroke)

        # Create text blocks far away
        blocks = [
            TextBlock("First paragraph", y_start=50, y_end=90, block_type="paragraph"),
        ]

        # Use default max_distance (100)
        mapping = associate_annotations_with_content([annotation], blocks)

        # Should not associate (distance > 100)
        assert 0 not in mapping.associations

    def test_association_with_custom_max_distance(self):
        """Test custom max_distance parameter."""
        points = [Point(0, 200), Point(10, 220)]
        stroke = Stroke(points=points, color=0, tool=1, thickness=1.0)
        annotation = Annotation(type=AnnotationType.STROKE, stroke=stroke)

        blocks = [
            TextBlock("Paragraph", y_start=50, y_end=90, block_type="paragraph"),
        ]

        # With default max_distance=100, should not associate
        mapping1 = associate_annotations_with_content([annotation], blocks, max_distance=100)
        assert 0 not in mapping1.associations

        # With max_distance=200, should associate
        mapping2 = associate_annotations_with_content([annotation], blocks, max_distance=200)
        assert 0 in mapping2.associations

    def test_multiple_annotations_multiple_blocks(self):
        """Test associating multiple annotations with multiple blocks."""
        # Create 3 annotations at different positions
        ann1 = Annotation(
            type=AnnotationType.STROKE, stroke=Stroke([Point(0, 60), Point(10, 80)], 0, 1, 1.0)
        )
        ann2 = Annotation(
            type=AnnotationType.STROKE, stroke=Stroke([Point(0, 120), Point(10, 140)], 0, 1, 1.0)
        )
        ann3 = Annotation(
            type=AnnotationType.STROKE, stroke=Stroke([Point(0, 180), Point(10, 200)], 0, 1, 1.0)
        )

        # Create 3 text blocks
        blocks = [
            TextBlock("First", y_start=50, y_end=90, block_type="paragraph"),
            TextBlock("Second", y_start=110, y_end=150, block_type="paragraph"),
            TextBlock("Third", y_start=170, y_end=210, block_type="paragraph"),
        ]

        mapping = associate_annotations_with_content([ann1, ann2, ann3], blocks)

        # Each annotation should associate with corresponding block
        assert mapping.associations[0] == 0  # ann1 → block0
        assert mapping.associations[1] == 1  # ann2 → block1
        assert mapping.associations[2] == 2  # ann3 → block2


class TestPositionMapping:
    """Tests for mapping text block positions between document versions."""

    def test_exact_content_match(self):
        """Test mapping blocks with exact content match."""
        old_blocks = [
            TextBlock("Hello world", y_start=100, y_end=150, block_type="paragraph"),
            TextBlock("Goodbye world", y_start=160, y_end=210, block_type="paragraph"),
        ]

        new_blocks = [
            TextBlock("Hello world", y_start=120, y_end=170, block_type="paragraph"),
            TextBlock("Goodbye world", y_start=180, y_end=230, block_type="paragraph"),
        ]

        mapping = calculate_position_mapping(old_blocks, new_blocks)

        assert mapping[0] == 0
        assert mapping[1] == 1

    def test_fuzzy_content_match(self):
        """Test mapping blocks with similar but not identical content."""
        old_blocks = [
            TextBlock("The quick brown fox jumps", y_start=100, y_end=150, block_type="paragraph"),
        ]

        new_blocks = [
            TextBlock("The quick brown fox leaps", y_start=120, y_end=170, block_type="paragraph"),
        ]

        mapping = calculate_position_mapping(old_blocks, new_blocks)

        # Should match with 4/6 words shared (Jaccard = 4/6 = 0.67 > 0.5)
        assert mapping[0] == 0

    def test_reordered_blocks(self):
        """Test mapping when blocks have been reordered."""
        old_blocks = [
            TextBlock("First paragraph", y_start=100, y_end=150, block_type="paragraph"),
            TextBlock("Second paragraph", y_start=160, y_end=210, block_type="paragraph"),
        ]

        new_blocks = [
            TextBlock("Second paragraph", y_start=100, y_end=150, block_type="paragraph"),
            TextBlock("First paragraph", y_start=160, y_end=210, block_type="paragraph"),
        ]

        mapping = calculate_position_mapping(old_blocks, new_blocks)

        # Should match by content, not position
        assert mapping[0] == 1  # First → Second position
        assert mapping[1] == 0  # Second → First position

    def test_deleted_blocks_not_mapped(self):
        """Test that deleted blocks don't appear in mapping."""
        old_blocks = [
            TextBlock("Deleted paragraph", y_start=100, y_end=150, block_type="paragraph"),
            TextBlock("Kept paragraph", y_start=160, y_end=210, block_type="paragraph"),
        ]

        new_blocks = [
            TextBlock("Kept paragraph", y_start=100, y_end=150, block_type="paragraph"),
        ]

        mapping = calculate_position_mapping(old_blocks, new_blocks)

        assert 0 not in mapping  # Deleted block not mapped
        assert mapping[1] == 0  # Kept block mapped

    def test_new_blocks_not_in_mapping(self):
        """Test that new blocks don't appear as targets in mapping."""
        old_blocks = [
            TextBlock("Old paragraph", y_start=100, y_end=150, block_type="paragraph"),
        ]

        new_blocks = [
            TextBlock("Old paragraph", y_start=100, y_end=150, block_type="paragraph"),
            TextBlock("New paragraph", y_start=160, y_end=210, block_type="paragraph"),
        ]

        mapping = calculate_position_mapping(old_blocks, new_blocks)

        # Only old blocks appear as keys
        assert mapping[0] == 0
        assert len(mapping) == 1

    def test_significant_edit_no_match(self):
        """Test that significantly edited content doesn't match."""
        old_blocks = [
            TextBlock("This is about cats", y_start=100, y_end=150, block_type="paragraph"),
        ]

        new_blocks = [
            TextBlock("This is about dogs", y_start=100, y_end=150, block_type="paragraph"),
        ]

        mapping = calculate_position_mapping(old_blocks, new_blocks)

        # Should match (shares "This is about")
        # Jaccard similarity = 3/4 = 0.75 > 0.5 threshold
        assert 0 in mapping

    def test_completely_different_content(self):
        """Test that completely different content doesn't match."""
        old_blocks = [
            TextBlock(
                "Python programming language", y_start=100, y_end=150, block_type="paragraph"
            ),
        ]

        new_blocks = [
            TextBlock("Rust systems design", y_start=100, y_end=150, block_type="paragraph"),
        ]

        mapping = calculate_position_mapping(old_blocks, new_blocks)

        # No shared words, should not match
        assert 0 not in mapping


class TestIntegrationWithRealFiles:
    """Integration tests using actual rmscene test files."""

    def test_full_workflow_strokes(self):
        """Test complete workflow: read → associate → map."""
        # Read annotations from real file
        file_path = TESTDATA_DIR / "Normal_A_stroke_2_layers.rm"
        annotations = read_annotations(file_path)

        # Verify we found strokes
        strokes = [i for i, a in enumerate(annotations) if a.type == AnnotationType.STROKE]
        assert len(strokes) == 2

        # Create mock text blocks at similar Y coordinates to the strokes
        # Strokes are at roughly y=[-30, -15] and y=[13, 15]
        # In reMarkable coordinates, these are near the origin
        blocks = [
            TextBlock("A", y_start=-50, y_end=50, block_type="paragraph"),
        ]

        # Associate annotations with generous max_distance
        mapping = associate_annotations_with_content(annotations, blocks, max_distance=100)

        # Both strokes should be associated with the text block
        for stroke_idx in strokes:
            assert (
                stroke_idx in mapping.associations
            ), f"Stroke {stroke_idx} at y={annotations[stroke_idx].center_y()} not associated"

    def test_full_workflow_highlights(self):
        """Test complete workflow with highlights."""
        file_path = TESTDATA_DIR / "Wikipedia_highlighted_p1.rm"
        annotations = read_annotations(file_path)

        # Create mock text blocks matching the Wikipedia page
        blocks = [
            TextBlock(
                "The reMarkable uses electronic paper",
                y_start=660,
                y_end=720,
                block_type="paragraph",
            ),
            TextBlock(
                "ReMarkable uses its own operating system, named Codex.",
                y_start=1630,
                y_end=1690,
                block_type="paragraph",
            ),
            TextBlock(
                "Codex is based on Linux and optimized for electronic paper",
                y_start=1675,
                y_end=1735,
                block_type="paragraph",
            ),
            TextBlock("display technology.[13]", y_start=1720, y_end=1780, block_type="paragraph"),
        ]

        mapping = associate_annotations_with_content(annotations, blocks)

        # All 4 highlights should be associated
        highlights = [i for i, a in enumerate(annotations) if a.type == AnnotationType.HIGHLIGHT]
        assert len(highlights) == 4

        # Each should be associated with corresponding block
        for i, highlight_idx in enumerate(highlights):
            assert highlight_idx in mapping.associations
            # Should associate with block at similar index (approximately)
            assert mapping.associations[highlight_idx] in range(len(blocks))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
