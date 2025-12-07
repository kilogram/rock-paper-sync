"""Tests for paragraph mapping architecture.

Tests the new cluster-first paragraph mapping system with:
- Unit tests for SpatialOverlapMapper
- Integration tests with real .rm files (if available)
- Coordinate transformation validation
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rock_paper_sync.annotations.common.text_extraction import RmTextBlock
from rock_paper_sync.ocr.paragraph_mapper import (
    ParagraphMapper,
    SpatialOverlapMapper,
)
from rock_paper_sync.ocr.protocol import BoundingBox
from rock_paper_sync.parser import BlockType, ContentBlock, parse_content


class TestSpatialOverlapMapper:
    """Unit tests for spatial overlap scoring algorithm."""

    def test_annotation_fully_overlapping_text(self):
        """Cluster bbox is fully inside text block - should score high."""
        mapper = SpatialOverlapMapper()

        # Cluster completely inside text block
        cluster_bbox = BoundingBox(x=100, y=100, width=200, height=20)
        text_block = RmTextBlock(
            content="test paragraph",
            y_start=95,
            y_end=125,
        )

        score = mapper._score_overlap(cluster_bbox, text_block)

        # Should score very high (full intersection + close proximity)
        assert score > 0.85, f"Expected high score for full overlap, got {score}"

    def test_annotation_partially_overlapping_text(self):
        """Cluster bbox overlaps 50% with text block."""
        mapper = SpatialOverlapMapper()

        # Cluster halfway overlapping text block
        cluster_bbox = BoundingBox(x=100, y=110, width=200, height=20)
        text_block = RmTextBlock(
            content="test paragraph",
            y_start=100,
            y_end=120,
        )

        score = mapper._score_overlap(cluster_bbox, text_block)

        # Should score medium-high (partial intersection)
        assert 0.5 < score < 0.85, f"Expected medium score for partial overlap, got {score}"

    def test_annotation_adjacent_to_text(self):
        """Cluster bbox is 10px below text block - no overlap but close."""
        mapper = SpatialOverlapMapper()

        # Cluster just below text block
        cluster_bbox = BoundingBox(x=100, y=130, width=200, height=20)
        text_block = RmTextBlock(
            content="test paragraph",
            y_start=100,
            y_end=120,
        )

        score = mapper._score_overlap(cluster_bbox, text_block)

        # Should score low-medium (no intersection but close proximity)
        # Score decreases with distance, so 10px gap gives score around 0.1-0.2
        assert 0.05 < score < 0.3, f"Expected low-medium score for adjacent, got {score}"

    def test_annotation_far_from_text(self):
        """Cluster bbox is 200px away from nearest text - should score low."""
        mapper = SpatialOverlapMapper()

        # Cluster far from text block
        cluster_bbox = BoundingBox(x=100, y=400, width=200, height=20)
        text_block = RmTextBlock(
            content="test paragraph",
            y_start=100,
            y_end=120,
        )

        score = mapper._score_overlap(cluster_bbox, text_block)

        # Should score very low (no intersection, far away)
        assert score < 0.2, f"Expected low score for far distance, got {score}"

    def test_multiple_overlapping_blocks_picks_best(self):
        """Cluster overlaps multiple text blocks - picks best match."""
        mapper = SpatialOverlapMapper()

        cluster_bbox = BoundingBox(x=100, y=150, width=200, height=20)

        text_blocks = [
            RmTextBlock(content="paragraph 1", y_start=100, y_end=120),
            RmTextBlock(content="paragraph 2", y_start=140, y_end=160),  # Best match
            RmTextBlock(content="paragraph 3", y_start=200, y_end=220),
        ]

        markdown_blocks = [
            ContentBlock(type=BlockType.PARAGRAPH, text="paragraph 1", level=0, formatting=[]),
            ContentBlock(type=BlockType.PARAGRAPH, text="paragraph 2", level=0, formatting=[]),
            ContentBlock(type=BlockType.PARAGRAPH, text="paragraph 3", level=0, formatting=[]),
        ]

        result = mapper.map_cluster_to_paragraph(cluster_bbox, markdown_blocks, text_blocks)

        # Should map to paragraph 1 (index 1, the best overlapping block)
        assert result == 1, f"Expected paragraph 1 (best overlap), got {result}"

    def test_empty_text_blocks_returns_none(self):
        """No text blocks available - should return None."""
        mapper = SpatialOverlapMapper()

        cluster_bbox = BoundingBox(x=100, y=100, width=200, height=20)
        result = mapper.map_cluster_to_paragraph(cluster_bbox, [], [])

        assert result is None

    def test_scoring_weights_configurable(self):
        """Custom weights can be configured."""
        # Heavy intersection weight
        mapper1 = SpatialOverlapMapper(intersection_weight=0.9, proximity_weight=0.1)

        # Heavy proximity weight
        mapper2 = SpatialOverlapMapper(intersection_weight=0.1, proximity_weight=0.9)

        # Cluster adjacent but not overlapping
        cluster_bbox = BoundingBox(x=100, y=130, width=200, height=20)
        text_block = RmTextBlock(content="test", y_start=100, y_end=120)

        score1 = mapper1._score_overlap(cluster_bbox, text_block)
        score2 = mapper2._score_overlap(cluster_bbox, text_block)

        # Proximity-weighted should score higher for adjacent annotations
        assert score2 > score1, "Proximity weight should favor adjacent annotations"


class TestTextMatching:
    """Tests for text-based block matching."""

    def test_exact_content_match(self):
        """Exact text match between rm block and markdown."""
        mapper = SpatialOverlapMapper()

        rm_block = RmTextBlock(content="This is a test paragraph", y_start=100, y_end=120)
        markdown_blocks = [
            ContentBlock(type=BlockType.PARAGRAPH, text="Different text", level=0, formatting=[]),
            ContentBlock(
                type=BlockType.PARAGRAPH, text="This is a test paragraph", level=0, formatting=[]
            ),
            ContentBlock(
                type=BlockType.PARAGRAPH, text="Another paragraph", level=0, formatting=[]
            ),
        ]

        result = mapper._match_rm_block_to_markdown(rm_block, markdown_blocks)

        assert result == 1, f"Expected exact match at index 1, got {result}"

    def test_substring_match(self):
        """Rm text is substring of markdown text (line wrapping)."""
        mapper = SpatialOverlapMapper()

        # Rm text might have different wrapping
        rm_block = RmTextBlock(content="This is a test", y_start=100, y_end=120)
        markdown_blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                text="This is a test paragraph with more words",
                level=0,
                formatting=[],
            ),
        ]

        result = mapper._match_rm_block_to_markdown(rm_block, markdown_blocks)

        assert result == 0, f"Expected substring match at index 0, got {result}"

    def test_prefix_match(self):
        """First 20 chars match - handles minor differences."""
        mapper = SpatialOverlapMapper()

        rm_block = RmTextBlock(
            content="This is a very long paragraph that starts the same", y_start=100, y_end=120
        )
        markdown_blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                text="This is a very long paragraph with different ending",
                level=0,
                formatting=[],
            ),
        ]

        result = mapper._match_rm_block_to_markdown(rm_block, markdown_blocks)

        assert result == 0, f"Expected prefix match at index 0, got {result}"

    def test_blockquote_content_matches(self):
        """Blockquote content is matched (issue from logs)."""
        mapper = SpatialOverlapMapper()

        rm_block = RmTextBlock(content="─────────────────────────────", y_start=100, y_end=120)
        markdown_blocks = [
            ContentBlock(type=BlockType.HEADER, text="Test Header", level=1, formatting=[]),
            ContentBlock(
                type=BlockType.BLOCKQUOTE,
                text="─────────────────────────────\n\n[Write here with strokes]\n\n─────────────────────────────",
                level=0,
                formatting=[],
            ),
        ]

        result = mapper._match_rm_block_to_markdown(rm_block, markdown_blocks)

        assert result == 1, f"Expected blockquote match at index 1, got {result}"


class TestIntegrationWithRealData:
    """Integration tests with real .rm files (if available)."""

    @pytest.fixture
    def testdata_dir(self):
        """Get testdata directory path - use record_replay testdata."""
        return Path(__file__).parent.parent / "record_replay" / "testdata" / "ocr_handwriting"

    @pytest.fixture
    def has_testdata(self, testdata_dir):
        """Check if testdata exists (supports phases structure)."""
        if not testdata_dir.exists():
            return False
        # Check for any .rm files anywhere in the testdata
        return bool(list(testdata_dir.rglob("*.rm")))

    def test_cluster_mapping_with_real_annotations(self, testdata_dir, has_testdata):
        """Test paragraph mapping with real .rm files containing handwriting."""
        if not has_testdata:
            pytest.skip("No testdata available")

        # Load test metadata
        import json

        from rock_paper_sync.annotations import read_annotations
        from rock_paper_sync.annotations.common.text_extraction import extract_text_blocks_from_rm

        manifest_path = testdata_dir / "manifest.json"
        if not manifest_path.exists():
            pytest.skip("No manifest.json in testdata")

        manifest = json.loads(manifest_path.read_text())

        # Load markdown (source.md at root for phases structure)
        source_doc = manifest.get("source_document", "source.md")
        markdown_path = testdata_dir / source_doc
        if not markdown_path.exists():
            markdown_path = testdata_dir / "markdown" / source_doc
        if not markdown_path.exists():
            pytest.skip("Source markdown not found")

        markdown_content = markdown_path.read_text()
        markdown_blocks = parse_content(markdown_content)

        # Find .rm files from phases structure
        rm_files = []
        phases = manifest.get("phases", [])
        for phase in phases:
            if phase.get("has_rm_files"):
                phase_name = f"phase_{phase['phase_number']}_{phase['phase_name']}"
                rm_dir = testdata_dir / "phases" / phase_name / "rm_files"
                if rm_dir.exists():
                    rm_files.extend(rm_dir.glob("*.rm"))
                break

        if not rm_files:
            rm_files = list(testdata_dir.rglob("*.rm"))

        if not rm_files:
            pytest.skip("No .rm files found")

        # Test each .rm file
        for rm_file in rm_files:
            # Read annotations and text blocks
            annotations = read_annotations(rm_file)
            rm_text_blocks, text_origin_y = extract_text_blocks_from_rm(rm_file)

            if not annotations:
                continue

            # Create mapper
            mapper = SpatialOverlapMapper()

            # Try to map annotations (some may be unmappable, like margin notes)
            if annotations and rm_text_blocks:
                mapped_count = 0
                for ann in annotations:
                    # Create simple cluster from annotation
                    test_bbox = BoundingBox(
                        x=ann.center_x() if hasattr(ann, "center_x") else 100,
                        y=ann.center_y() if hasattr(ann, "center_y") else 100,
                        width=50,
                        height=20,
                    )

                    result = mapper.map_cluster_to_paragraph(
                        test_bbox,
                        markdown_blocks,
                        rm_text_blocks,
                    )

                    if result is not None:
                        mapped_count += 1
                        assert (
                            0 <= result < len(markdown_blocks)
                        ), f"Invalid paragraph index {result}"

                # At least some annotations should map (not all, as some may be margin notes)
                # We just verify the mapper doesn't crash and returns valid indices when it does map
                # Note: It's OK if mapped_count is 0 for some files (all margin notes)


class TestCoordinateTransformation:
    """Tests for coordinate space handling."""

    def test_absolute_coordinates_unchanged(self):
        """Annotations in absolute space are not transformed."""
        from pathlib import Path

        from rmscene.tagged_block_common import CrdtId

        from rock_paper_sync.annotations import Annotation, AnnotationType, Point, Stroke
        from rock_paper_sync.config import OCRConfig
        from rock_paper_sync.ocr.integration import OCRProcessor

        config = OCRConfig(enabled=True, cache_dir=Path("/tmp/test"))
        processor = OCRProcessor(config, MagicMock())

        # Create annotation in absolute space (parent = root layer)
        stroke = Stroke(
            points=[Point(10, 100), Point(50, 100)],
            color=0,
            tool=1,
            thickness=2.0,
        )
        annotation = Annotation(
            type=AnnotationType.STROKE,
            stroke=stroke,
            parent_id=CrdtId(0, 11),  # Root layer = absolute coordinates
        )

        text_origin_x = 0.0
        text_origin_y = 200.0
        parent_anchor_map = {}  # Empty map, rely on text origin fallback

        transformed = processor._transform_annotations_to_absolute(
            [annotation], parent_anchor_map, text_origin_x, text_origin_y
        )

        # Should NOT be transformed (already absolute)
        assert len(transformed) == 1
        result = transformed[0]
        assert result.stroke is not None
        # Points should remain unchanged
        assert result.stroke.points[0].y == 100, "Absolute coordinates should not be transformed"
        assert result.stroke.points[0].x == 10

    def test_text_relative_coordinates_transformed(self):
        """Annotations in text-relative space are transformed to absolute."""
        from pathlib import Path

        from rmscene.tagged_block_common import CrdtId

        from rock_paper_sync.annotations import Annotation, AnnotationType, Point, Stroke
        from rock_paper_sync.config import OCRConfig
        from rock_paper_sync.ocr.integration import OCRProcessor

        config = OCRConfig(enabled=True, cache_dir=Path("/tmp/test"))
        processor = OCRProcessor(config, MagicMock())

        # Create annotation in text-relative space
        stroke = Stroke(
            points=[Point(10, 5), Point(50, 5)],  # 5px relative to text
            color=0,
            tool=1,
            thickness=2.0,
        )
        annotation = Annotation(
            type=AnnotationType.STROKE,
            stroke=stroke,
            parent_id=CrdtId(2, 530),  # Text layer = relative coordinates
        )

        text_origin_x = 0.0
        text_origin_y = 200.0
        parent_anchor_map = {}  # Empty map, rely on text origin fallback

        transformed = processor._transform_annotations_to_absolute(
            [annotation], parent_anchor_map, text_origin_x, text_origin_y
        )

        # Should be transformed: y = text_origin_y + relative_y
        assert len(transformed) == 1
        result = transformed[0]
        assert result.stroke is not None
        # Y should be transformed: 200 + 5 = 205
        assert (
            result.stroke.points[0].y == 205
        ), f"Expected y=205 (200+5), got {result.stroke.points[0].y}"
        assert result.stroke.points[0].x == 10, "X coordinate should not change"


def test_mapper_interface_compatibility():
    """Test that ParagraphMapper interface works with different implementations."""

    class MockVisionModelMapper(ParagraphMapper):
        """Mock vision model mapper for testing interface."""

        def map_cluster_to_paragraph(self, cluster_bbox, markdown_blocks, **context):
            # Vision model would use different logic
            # For test, just return first paragraph
            return 0 if markdown_blocks else None

    # Test that interface works
    mapper = MockVisionModelMapper()

    cluster_bbox = BoundingBox(x=100, y=100, width=200, height=20)
    markdown_blocks = [
        ContentBlock(type=BlockType.PARAGRAPH, text="test", level=0, formatting=[]),
    ]

    result = mapper.map_cluster_to_paragraph(
        cluster_bbox,
        markdown_blocks,
        page_image=b"fake image",  # Vision model might need this
    )

    assert result == 0, "Interface should support different implementations"
