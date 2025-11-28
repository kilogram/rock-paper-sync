"""Tests for annotation anchor system (Phase 1).

Tests the new anchor abstractions that hide RM v6 format complexity:
- PagePosition: Physical page positioning
- BoundingBox: Spatial bounding and overlap calculations
- TextAnchor: Text-based anchoring with context
- AnnotationAnchor: Unified anchor for matching and corrections
"""

import pytest

from rock_paper_sync.annotations.common.anchors import (
    AnnotationAnchor,
    BoundingBox,
    PagePosition,
    TextAnchor,
)


class TestPagePosition:
    """Tests for PagePosition."""

    def test_distance_same_page(self):
        """Test distance calculation on same page."""
        pos1 = PagePosition(page_num=0, x=100.0, y=200.0)
        pos2 = PagePosition(page_num=0, x=103.0, y=204.0)

        distance = pos1.distance_to(pos2)
        assert distance == pytest.approx(5.0)  # sqrt(3^2 + 4^2)

    def test_distance_different_pages(self):
        """Test distance returns infinity for different pages."""
        pos1 = PagePosition(page_num=0, x=100.0, y=200.0)
        pos2 = PagePosition(page_num=1, x=100.0, y=200.0)

        distance = pos1.distance_to(pos2)
        assert distance == float("inf")

    def test_similarity_score_identical(self):
        """Test similarity score for identical positions."""
        pos1 = PagePosition(page_num=0, x=100.0, y=200.0)
        pos2 = PagePosition(page_num=0, x=100.0, y=200.0)

        score = pos1.similarity_score(pos2)
        assert score == 1.0

    def test_similarity_score_far_apart(self):
        """Test similarity score for positions far apart."""
        pos1 = PagePosition(page_num=0, x=100.0, y=200.0)
        pos2 = PagePosition(page_num=0, x=500.0, y=200.0)  # 400 pixels away

        score = pos1.similarity_score(pos2, max_distance=200.0)
        assert score == 0.0  # Beyond max_distance

    def test_similarity_score_medium_distance(self):
        """Test similarity score for medium distance."""
        pos1 = PagePosition(page_num=0, x=100.0, y=200.0)
        pos2 = PagePosition(page_num=0, x=200.0, y=200.0)  # 100 pixels away

        score = pos1.similarity_score(pos2, max_distance=200.0)
        assert score == pytest.approx(0.5)  # 1.0 - (100/200)


class TestBoundingBox:
    """Tests for BoundingBox."""

    def test_center_calculation(self):
        """Test center point calculation."""
        bbox = BoundingBox(x=100.0, y=200.0, width=50.0, height=80.0)

        assert bbox.center_x == 125.0
        assert bbox.center_y == 240.0
        assert bbox.center == (125.0, 240.0)

    def test_overlaps_true(self):
        """Test overlapping boxes."""
        bbox1 = BoundingBox(x=100.0, y=100.0, width=50.0, height=50.0)
        bbox2 = BoundingBox(x=120.0, y=120.0, width=50.0, height=50.0)

        assert bbox1.overlaps(bbox2)
        assert bbox2.overlaps(bbox1)  # Symmetric

    def test_overlaps_false(self):
        """Test non-overlapping boxes."""
        bbox1 = BoundingBox(x=100.0, y=100.0, width=50.0, height=50.0)
        bbox2 = BoundingBox(x=200.0, y=200.0, width=50.0, height=50.0)

        assert not bbox1.overlaps(bbox2)
        assert not bbox2.overlaps(bbox1)

    def test_overlap_area(self):
        """Test overlap area calculation."""
        bbox1 = BoundingBox(x=0.0, y=0.0, width=100.0, height=100.0)
        bbox2 = BoundingBox(x=50.0, y=50.0, width=100.0, height=100.0)

        area = bbox1.overlap_area(bbox2)
        assert area == 2500.0  # 50x50 overlap

    def test_iou_identical(self):
        """Test IoU for identical boxes."""
        bbox1 = BoundingBox(x=100.0, y=100.0, width=50.0, height=50.0)
        bbox2 = BoundingBox(x=100.0, y=100.0, width=50.0, height=50.0)

        iou = bbox1.iou(bbox2)
        assert iou == 1.0

    def test_iou_partial_overlap(self):
        """Test IoU for partially overlapping boxes."""
        bbox1 = BoundingBox(x=0.0, y=0.0, width=100.0, height=100.0)
        bbox2 = BoundingBox(x=50.0, y=50.0, width=100.0, height=100.0)

        # Intersection: 50x50 = 2500
        # Union: 10000 + 10000 - 2500 = 17500
        # IoU: 2500/17500 = 1/7 ≈ 0.1429
        iou = bbox1.iou(bbox2)
        assert iou == pytest.approx(0.1429, abs=0.001)

    def test_iou_no_overlap(self):
        """Test IoU for non-overlapping boxes."""
        bbox1 = BoundingBox(x=0.0, y=0.0, width=50.0, height=50.0)
        bbox2 = BoundingBox(x=100.0, y=100.0, width=50.0, height=50.0)

        iou = bbox1.iou(bbox2)
        assert iou == 0.0


class TestTextAnchor:
    """Tests for TextAnchor."""

    def test_match_in_text_exact(self):
        """Test exact text matching."""
        anchor = TextAnchor(
            content="brown fox", context_before="The quick ", context_after=" jumps"
        )

        text = "The quick brown fox jumps over the lazy dog"
        offset = anchor.match_in_text(text)

        assert offset == 10  # Position of "brown fox"

    def test_match_in_text_fuzzy(self):
        """Test fuzzy text matching."""
        anchor = TextAnchor(
            content="brown fox", context_before="The quick ", context_after=" jumps"
        )

        # Slightly modified text
        text = "The quick brown foxx jumps over the lazy dog"
        offset = anchor.match_in_text(text, fuzzy_threshold=0.8)

        assert offset is not None  # Should find fuzzy match

    def test_match_in_text_no_match(self):
        """Test no match when text differs too much."""
        anchor = TextAnchor(content="elephant", context_before="", context_after="")

        text = "The quick brown fox jumps over the lazy dog"
        offset = anchor.match_in_text(text)

        assert offset is None

    def test_similarity_score_perfect(self):
        """Test similarity score for perfect match."""
        anchor = TextAnchor(
            content="brown fox", context_before="The quick ", context_after=" jumps"
        )

        text = "The quick brown fox jumps over the lazy dog"
        score = anchor.similarity_score(text)

        assert score >= 0.9  # Near-perfect match with context

    def test_similarity_score_no_match(self):
        """Test similarity score when no match found."""
        anchor = TextAnchor(content="elephant", context_before="", context_after="")

        text = "The quick brown fox jumps over the lazy dog"
        score = anchor.similarity_score(text)

        assert score == 0.0


class TestAnnotationAnchor:
    """Tests for AnnotationAnchor."""

    def test_from_highlight(self):
        """Test creating anchor from highlight."""
        anchor = AnnotationAnchor.from_highlight(
            highlight_text="important phrase",
            page_num=0,
            position=(100.0, 200.0),
            bounding_box=(90.0, 180.0, 120.0, 40.0),
            paragraph_index=5,
            context_before="This is an ",
            context_after=" in the text",
            color=3,
        )

        assert anchor.annotation_type == "highlight"
        assert anchor.page.page_num == 0
        assert anchor.page.x == 100.0
        assert anchor.page.y == 200.0
        assert anchor.bbox is not None
        assert anchor.bbox.x == 90.0
        assert anchor.text is not None
        assert anchor.text.content == "important phrase"
        assert anchor.metadata["color"] == 3

    def test_from_stroke(self):
        """Test creating anchor from stroke."""
        anchor = AnnotationAnchor.from_stroke(
            page_num=0,
            position=(150.0, 250.0),
            bounding_box=(140.0, 240.0, 20.0, 20.0),
            paragraph_index=3,
            ocr_text="handwritten",
            context_before="Before ",
            context_after=" after",
            image_hash="abc123",
            confidence=0.95,
        )

        assert anchor.annotation_type == "stroke"
        assert anchor.page.page_num == 0
        assert anchor.bbox is not None
        assert anchor.text is not None
        assert anchor.text.content == "handwritten"
        assert anchor.metadata["image_hash"] == "abc123"
        assert anchor.metadata["confidence"] == 0.95

    def test_match_score_text_only(self):
        """Test match scoring with text matching."""
        anchor = AnnotationAnchor.from_highlight(
            highlight_text="important phrase",
            page_num=0,
            position=(100.0, 200.0),
            paragraph_index=5,
            context_before="This is an ",
            context_after=" in the text",
        )

        # Perfect text match
        score = anchor.match_score(paragraph_text="This is an important phrase in the text")

        assert score > 0.8  # Strong match due to text

    def test_match_score_position_and_text(self):
        """Test match scoring with position and text."""
        anchor = AnnotationAnchor.from_highlight(
            highlight_text="important phrase",
            page_num=0,
            position=(100.0, 200.0),
            paragraph_index=5,
            context_before="This is an ",
            context_after=" in the text",
        )

        # Same position and text
        score = anchor.match_score(
            paragraph_text="This is an important phrase in the text", position=(100.0, 200.0)
        )

        assert score > 0.9  # Very strong match

    def test_match_score_all_signals(self):
        """Test match scoring with all signals (text, position, bbox)."""
        anchor = AnnotationAnchor.from_highlight(
            highlight_text="important phrase",
            page_num=0,
            position=(100.0, 200.0),
            bounding_box=(90.0, 180.0, 120.0, 40.0),
            paragraph_index=5,
            context_before="This is an ",
            context_after=" in the text",
        )

        # Perfect match on all dimensions
        score = anchor.match_score(
            paragraph_text="This is an important phrase in the text",
            position=(100.0, 200.0),
            bbox=(90.0, 180.0, 120.0, 40.0),
        )

        assert score > 0.95  # Near-perfect match

    def test_match_score_poor_match(self):
        """Test match scoring with poor match."""
        anchor = AnnotationAnchor.from_highlight(
            highlight_text="important phrase",
            page_num=0,
            position=(100.0, 200.0),
            paragraph_index=5,
        )

        # Different text, different position
        score = anchor.match_score(
            paragraph_text="Completely different text here", position=(500.0, 600.0)
        )

        assert score < 0.3  # Poor match

    def test_content_derived_id_stability(self):
        """Test that content-derived IDs are stable."""
        anchor1 = AnnotationAnchor.from_highlight(
            highlight_text="same text", page_num=0, position=(100.0, 200.0), paragraph_index=5
        )

        anchor2 = AnnotationAnchor.from_highlight(
            highlight_text="same text", page_num=0, position=(100.0, 200.0), paragraph_index=5
        )

        # IDs should be identical for same content/position
        assert anchor1.annotation_id == anchor2.annotation_id

    def test_content_derived_id_different(self):
        """Test that different content produces different IDs."""
        anchor1 = AnnotationAnchor.from_highlight(
            highlight_text="text one", page_num=0, position=(100.0, 200.0), paragraph_index=5
        )

        anchor2 = AnnotationAnchor.from_highlight(
            highlight_text="text two", page_num=0, position=(100.0, 200.0), paragraph_index=5
        )

        # IDs should differ for different content
        assert anchor1.annotation_id != anchor2.annotation_id
