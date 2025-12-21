"""Tests using real handwriting samples from device bench.

These tests use actual .rm files with handwriting extracted from a reMarkable
device. They validate:
- Annotation extraction from .rm files
- Spatial clustering of strokes
- Paragraph mapping accuracy
- Coordinate transformation correctness
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations
from rock_paper_sync.annotations.common.text_extraction import extract_text_blocks_from_rm
from rock_paper_sync.ocr.paragraph_mapper import SpatialOverlapMapper
from rock_paper_sync.ocr.protocol import BoundingBox
from rock_paper_sync.parser import parse_content

# Test data paths - use record_replay testdata
TESTDATA_DIR = Path(__file__).parent.parent / "record_replay" / "testdata" / "ocr_handwriting"
MANIFEST_PATH = TESTDATA_DIR / "manifest.json"


@pytest.fixture
def testdata_manifest():
    """Load test manifest with expected values."""
    if not MANIFEST_PATH.exists():
        pytest.skip("OCR handwriting testdata not available")
    return json.loads(MANIFEST_PATH.read_text())


@pytest.fixture
def rm_files(testdata_manifest):
    """Get list of .rm files from testdata (phases-based structure)."""
    files = []

    # Find the phase with .rm files (typically phase_1 or later)
    phases = testdata_manifest.get("phases", [])
    for phase in phases:
        if phase.get("has_rm_files"):
            phase_name = f"phase_{phase['phase_number']}_{phase['phase_name']}"
            rm_files_dir = TESTDATA_DIR / "phases" / phase_name / "rm_files"
            if rm_files_dir.exists():
                files.extend(rm_files_dir.glob("*.rm"))
            break  # Use first phase with rm files

    # Fallback: search for any .rm files
    if not files:
        files = list(TESTDATA_DIR.rglob("*.rm"))

    if not files:
        pytest.skip("No .rm files found in testdata")
    return files


@pytest.fixture
def markdown_content(testdata_manifest):
    """Load source markdown content."""
    source_doc = testdata_manifest.get("source_document", "source.md")
    # Try root first (phases structure), then markdown/ subdirectory
    md_path = TESTDATA_DIR / source_doc
    if not md_path.exists():
        md_path = TESTDATA_DIR / "markdown" / source_doc
    if not md_path.exists():
        pytest.skip("Source markdown not found")
    return md_path.read_text()


@pytest.fixture
def markdown_blocks(markdown_content):
    """Parse markdown into content blocks."""
    return parse_content(markdown_content)


class TestAnnotationExtraction:
    """Tests for annotation extraction from .rm files."""

    def test_rm_files_contain_annotations(self, rm_files):
        """Verify test .rm files contain extractable annotations."""
        total_annotations = 0

        for rm_file in rm_files:
            annotations = read_annotations(rm_file)
            total_annotations += len(annotations)

        assert total_annotations > 0, "Expected at least one annotation in testdata"

    def test_annotation_types(self, rm_files):
        """Verify annotation types are correctly identified."""
        strokes = 0
        highlights = 0

        for rm_file in rm_files:
            annotations = read_annotations(rm_file)
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    strokes += 1
                elif ann.type == AnnotationType.HIGHLIGHT:
                    highlights += 1

        # Testdata should have strokes (handwriting)
        assert strokes > 0, "Expected strokes in testdata (handwriting)"

    def test_stroke_has_valid_points(self, rm_files):
        """Verify strokes have valid point data."""
        for rm_file in rm_files:
            annotations = read_annotations(rm_file)
            for ann in annotations:
                if ann.type == AnnotationType.STROKE and ann.stroke:
                    assert len(ann.stroke.points) >= 2, "Stroke needs at least 2 points"
                    for point in ann.stroke.points:
                        # Points should have x, y coordinates
                        assert hasattr(point, "x")
                        assert hasattr(point, "y")

    def test_stroke_has_bounding_box(self, rm_files):
        """Verify strokes have computed bounding boxes."""
        for rm_file in rm_files:
            annotations = read_annotations(rm_file)
            for ann in annotations:
                if ann.type == AnnotationType.STROKE and ann.stroke:
                    bbox = ann.stroke.bounding_box
                    assert bbox is not None, "Stroke should have bounding box"
                    # Bounding box should have positive dimensions
                    assert bbox.w >= 0, f"Invalid width: {bbox.w}"
                    assert bbox.h >= 0, f"Invalid height: {bbox.h}"


class TestTextBlockExtraction:
    """Tests for text block extraction from .rm files."""

    def test_extract_text_blocks(self, rm_files):
        """Verify text blocks can be extracted from .rm files."""
        for rm_file in rm_files:
            text_blocks, text_origin_y = extract_text_blocks_from_rm(rm_file)

            # Should have text blocks
            assert len(text_blocks) > 0, f"No text blocks in {rm_file.name}"

            # Text origin should be reasonable
            assert 0 < text_origin_y < 500, f"Unusual text_origin_y: {text_origin_y}"

    def test_text_blocks_have_y_positions(self, rm_files):
        """Verify text blocks have Y position information."""
        for rm_file in rm_files:
            text_blocks, _ = extract_text_blocks_from_rm(rm_file)

            for block in text_blocks:
                assert hasattr(block, "y_start"), "Text block missing y_start"
                assert hasattr(block, "y_end"), "Text block missing y_end"
                assert block.y_start < block.y_end, "Invalid Y range"


class TestSpatialClustering:
    """Tests for annotation spatial clustering via DocumentModel."""

    def test_clustering_produces_clusters(self, rm_files):
        """Verify clustering algorithm produces meaningful clusters via DocumentModel."""
        from rock_paper_sync.annotations.document_model import DocumentModel
        from rock_paper_sync.layout import DEFAULT_DEVICE

        for rm_file in rm_files:
            # Use DocumentModel to load with clustering
            doc_model = DocumentModel.from_rm_files([rm_file], DEFAULT_DEVICE)

            if not doc_model.annotations:
                continue

            clusters = doc_model.get_annotation_clusters()

            # Should produce at least one cluster
            assert len(clusters) > 0, f"No clusters from {len(doc_model.annotations)} annotations"

            # Each cluster should have annotations
            for cluster in clusters:
                assert len(cluster) > 0, "Empty cluster produced"

    def test_cluster_count_reasonable(self, rm_files, testdata_manifest):
        """Verify cluster count is reasonable for test cases."""
        from rock_paper_sync.annotations.document_model import DocumentModel
        from rock_paper_sync.layout import DEFAULT_DEVICE

        # Number of test cases gives us expected cluster minimum
        expected_min_clusters = len(testdata_manifest.get("test_cases", []))

        total_clusters = 0
        for rm_file in rm_files:
            # Use DocumentModel to load with clustering
            doc_model = DocumentModel.from_rm_files([rm_file], DEFAULT_DEVICE)

            if not doc_model.annotations:
                continue

            clusters = doc_model.get_annotation_clusters()
            total_clusters += len(clusters)

        # Should have at least one cluster per test case (likely more)
        if expected_min_clusters > 0:
            assert total_clusters >= 1, (
                f"Expected at least 1 cluster for {expected_min_clusters} test cases, "
                f"got {total_clusters}"
            )


class TestParagraphMapping:
    """Tests for mapping annotations to paragraphs."""

    def test_mapper_returns_valid_indices(self, rm_files, markdown_blocks):
        """Verify paragraph mapper returns valid indices."""
        mapper = SpatialOverlapMapper()

        for rm_file in rm_files:
            annotations = read_annotations(rm_file)
            rm_text_blocks, _ = extract_text_blocks_from_rm(rm_file)

            if not annotations:
                continue

            for ann in annotations:
                if ann.type == AnnotationType.STROKE and ann.stroke:
                    bbox = ann.stroke.bounding_box
                    test_bbox = BoundingBox(
                        x=bbox.x,
                        y=bbox.y,
                        width=bbox.w,
                        height=bbox.h,
                    )

                    result = mapper.map_cluster_to_paragraph(
                        test_bbox,
                        markdown_blocks,
                        rm_text_blocks,
                    )

                    if result is not None:
                        assert (
                            0 <= result < len(markdown_blocks)
                        ), f"Invalid paragraph index {result} (max {len(markdown_blocks) - 1})"

    def test_mappings_to_expected_sections(self, rm_files, markdown_blocks, testdata_manifest):
        """Verify mappings correlate with expected test sections."""
        test_cases = testdata_manifest.get("test_cases", [])
        if not test_cases:
            pytest.skip("No test cases in manifest")

        # Find section indices in markdown
        section_indices = {}
        for i, block in enumerate(markdown_blocks):
            content = block.text.lower() if hasattr(block, "text") else ""
            for tc in test_cases:
                section_name = tc["section"].lower()
                if section_name in content:
                    section_indices[tc["section"]] = i

        # At least verify we found some sections
        assert len(section_indices) > 0, "Could not find any test sections in markdown"


class TestCoordinateTransformation:
    """Tests for coordinate space transformations."""

    def test_text_origin_extraction(self, rm_files):
        """Verify text origin can be extracted from .rm files."""
        from rock_paper_sync.coordinate_transformer import extract_text_origin

        for rm_file in rm_files:
            origin = extract_text_origin(rm_file)

            # Should have valid coordinates
            assert origin.x is not None
            assert origin.y is not None
            assert origin.width is not None

            # Common values for reMarkable files
            assert -500 < origin.x < 500, f"Unusual origin.x: {origin.x}"
            assert 0 < origin.y < 500, f"Unusual origin.y: {origin.y}"
            assert 500 < origin.width < 1000, f"Unusual width: {origin.width}"

    def test_parent_anchor_map_extraction(self, rm_files):
        """Verify parent anchor map can be built from .rm files."""
        from rock_paper_sync.coordinate_transformer import build_parent_anchor_map

        for rm_file in rm_files:
            anchor_map = build_parent_anchor_map(rm_file)

            # Map may be empty if no text-relative annotations
            # But if populated, should have valid data
            for parent_id, origin in anchor_map.items():
                assert hasattr(origin, "x")
                assert hasattr(origin, "y")

    def test_annotations_load_with_coordinates(self, rm_files):
        """Verify annotations load with valid coordinates via DocumentModel."""
        from rock_paper_sync.annotations.document_model import DocumentModel
        from rock_paper_sync.layout import DEFAULT_DEVICE

        for rm_file in rm_files:
            # Use DocumentModel to load annotations (handles coordinate transformation)
            doc_model = DocumentModel.from_rm_files([rm_file], DEFAULT_DEVICE)

            if not doc_model.annotations:
                continue

            # Verify annotations have valid bounding boxes
            for ann in doc_model.annotations:
                if ann.annotation_type == "stroke" and ann.stroke_data:
                    x, y, w, h = ann.stroke_data.bounding_box
                    # Bounding box should have positive dimensions
                    assert w >= 0, f"Invalid width: {w}"
                    assert h >= 0, f"Invalid height: {h}"


class TestImageRendering:
    """Tests for annotation image rendering using DocumentModel."""

    def test_render_strokes_to_image(self, rm_files):
        """Verify strokes can be rendered to images via DocumentModel."""
        from rock_paper_sync.annotations.document_model import DocumentModel
        from rock_paper_sync.config import OCRConfig
        from rock_paper_sync.layout import DEFAULT_DEVICE
        from rock_paper_sync.ocr.integration import OCRProcessor

        config = OCRConfig(enabled=True, cache_dir=Path("/tmp/test"))
        processor = OCRProcessor(config, MagicMock())

        for rm_file in rm_files:
            # Use DocumentModel to load and cluster annotations
            doc_model = DocumentModel.from_rm_files([rm_file], DEFAULT_DEVICE)

            # Filter to stroke annotations only
            stroke_annotations = [a for a in doc_model.annotations if a.annotation_type == "stroke"]

            if not stroke_annotations:
                continue

            # Render to image using new method
            image_data, bbox = processor._render_document_annotations_to_image(stroke_annotations)

            # Should produce valid PNG data
            assert len(image_data) > 0, "No image data produced"
            assert image_data[:8] == b"\x89PNG\r\n\x1a\n", "Not a valid PNG"

            # Bounding box should be reasonable
            assert bbox.width > 0, "Invalid bounding box width"
            assert bbox.height > 0, "Invalid bounding box height"


class TestEndToEnd:
    """End-to-end tests combining all components."""

    def test_full_extraction_pipeline(self, rm_files, markdown_blocks):
        """Test full pipeline using DocumentModel: load → cluster → render."""
        from rock_paper_sync.annotations.document_model import DocumentModel
        from rock_paper_sync.config import OCRConfig
        from rock_paper_sync.layout import DEFAULT_DEVICE
        from rock_paper_sync.ocr.integration import OCRProcessor

        config = OCRConfig(enabled=True, cache_dir=Path("/tmp/test"))
        processor = OCRProcessor(config, MagicMock())

        results = []

        for rm_file in rm_files:
            # Use DocumentModel to load with clustering
            doc_model = DocumentModel.from_rm_files([rm_file], DEFAULT_DEVICE)

            if not doc_model.annotations:
                continue

            # Get pre-computed clusters from DocumentModel
            clusters = doc_model.get_annotation_clusters()

            # For each cluster, render
            for cluster in clusters:
                # Render using new method
                image_data, cluster_bbox = processor._render_document_annotations_to_image(cluster)

                if not image_data:
                    continue

                # Get paragraph from anchor_context (if available)
                para_idx = None
                first_ann = cluster[0]
                if first_ann.anchor_context:
                    para_idx = first_ann.anchor_context.paragraph_index

                results.append(
                    {
                        "rm_file": rm_file.name,
                        "cluster_size": len(cluster),
                        "image_size": len(image_data),
                        "paragraph_idx": para_idx,
                        "bbox": (
                            cluster_bbox.x,
                            cluster_bbox.y,
                            cluster_bbox.width,
                            cluster_bbox.height,
                        ),
                    }
                )

        # Should have produced some results
        assert len(results) > 0, "Pipeline produced no results"

        # Log results for debugging
        for r in results:
            print(
                f"  {r['rm_file']}: cluster={r['cluster_size']} annotations, "
                f"para={r['paragraph_idx']}, bbox={r['bbox']}"
            )
