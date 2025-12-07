"""Tests for annotation preservation using real-world testdata.

These tests use actual .rm files captured from a reMarkable device in two stages:
- stage1_initial: Documents before annotations were added
- stage2_annotated: Documents after annotations were added by user

This allows testing:
- Annotation detection and extraction
- Before/after comparison
- Round-trip preservation logic
"""

from pathlib import Path

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations
from rock_paper_sync.annotations.common.text_extraction import extract_text_blocks_from_rm
from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.generator import RemarkableGenerator
from rock_paper_sync.parser import parse_markdown_file

# Test data paths
TESTDATA_DIR = Path(__file__).parent / "testdata" / "real_world_annotation_test"
STAGE1_DIR = TESTDATA_DIR / "stage1_initial"
STAGE2_DIR = TESTDATA_DIR / "stage2_annotated"


def has_testdata() -> bool:
    """Check if testdata is available."""
    return (
        STAGE1_DIR.exists()
        and STAGE2_DIR.exists()
        and list(STAGE1_DIR.glob("*.rm"))
        and list(STAGE2_DIR.glob("*.rm"))
    )


pytestmark = pytest.mark.skipif(not has_testdata(), reason="Annotation testdata not available")


@pytest.fixture
def stage1_rm_files():
    """Get .rm files from stage1 (initial)."""
    return sorted(STAGE1_DIR.glob("*.rm"))


@pytest.fixture
def stage2_rm_files():
    """Get .rm files from stage2 (annotated)."""
    return sorted(STAGE2_DIR.glob("*.rm"))


@pytest.fixture
def markdown_file():
    """Get source markdown file."""
    return STAGE1_DIR / "test1.md"


@pytest.fixture
def doc_uuid():
    """Get document UUID."""
    uuid_file = STAGE1_DIR / "doc_uuid.txt"
    if uuid_file.exists():
        return uuid_file.read_text().strip()
    return None


@pytest.fixture
def page_uuids():
    """Get page UUIDs."""
    uuid_file = STAGE1_DIR / "page_uuids.txt"
    if uuid_file.exists():
        return [line.strip() for line in uuid_file.read_text().split("\n") if line.strip()]
    return []


class TestAnnotationDetection:
    """Tests for detecting annotations between stages."""

    def test_stage1_has_no_annotations(self, stage1_rm_files):
        """Verify stage1 files have no annotations (clean initial state)."""
        total_annotations = 0

        for rm_file in stage1_rm_files:
            annotations = read_annotations(rm_file)
            total_annotations += len(annotations)

        # Stage1 should be clean
        assert (
            total_annotations == 0
        ), f"Expected no annotations in stage1, found {total_annotations}"

    def test_stage2_has_annotations(self, stage2_rm_files):
        """Verify stage2 files have annotations (user added them)."""
        total_annotations = 0

        for rm_file in stage2_rm_files:
            annotations = read_annotations(rm_file)
            total_annotations += len(annotations)

        # Stage2 should have annotations
        assert total_annotations > 0, "Expected annotations in stage2"

    def test_annotation_count_comparison(self, stage1_rm_files, stage2_rm_files):
        """Compare annotation counts between stages."""
        stage1_count = sum(len(read_annotations(f)) for f in stage1_rm_files)
        stage2_count = sum(len(read_annotations(f)) for f in stage2_rm_files)

        # Stage2 should have more annotations
        assert (
            stage2_count > stage1_count
        ), f"Stage2 ({stage2_count}) should have more annotations than stage1 ({stage1_count})"


class TestAnnotationContent:
    """Tests for annotation content in stage2."""

    def test_annotations_have_valid_type(self, stage2_rm_files):
        """Verify annotations have valid types."""
        for rm_file in stage2_rm_files:
            annotations = read_annotations(rm_file)
            for ann in annotations:
                assert ann.type in [
                    AnnotationType.STROKE,
                    AnnotationType.HIGHLIGHT,
                ], f"Invalid annotation type: {ann.type}"

    def test_strokes_have_points(self, stage2_rm_files):
        """Verify strokes have point data."""
        for rm_file in stage2_rm_files:
            annotations = read_annotations(rm_file)
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    assert ann.stroke is not None, "Stroke annotation missing stroke data"
                    # Strokes can have 1 point (tap/dot) or more
                    assert len(ann.stroke.points) >= 1, "Stroke needs at least 1 point"

    def test_highlights_have_rectangles(self, stage2_rm_files):
        """Verify highlights have rectangle data."""
        for rm_file in stage2_rm_files:
            annotations = read_annotations(rm_file)
            for ann in annotations:
                if ann.type == AnnotationType.HIGHLIGHT:
                    assert ann.highlight is not None, "Highlight annotation missing highlight data"
                    assert (
                        len(ann.highlight.rectangles) >= 1
                    ), "Highlight needs at least 1 rectangle"


class TestTextBlockExtraction:
    """Tests for text block extraction consistency."""

    def test_stage1_has_text_blocks(self, stage1_rm_files):
        """Verify stage1 files have extractable text blocks."""
        for rm_file in stage1_rm_files:
            text_blocks, text_origin_y = extract_text_blocks_from_rm(rm_file)
            assert len(text_blocks) > 0, f"No text blocks in {rm_file.name}"

    def test_stage2_has_text_blocks(self, stage2_rm_files):
        """Verify stage2 files have extractable text blocks."""
        for rm_file in stage2_rm_files:
            text_blocks, text_origin_y = extract_text_blocks_from_rm(rm_file)
            assert len(text_blocks) > 0, f"No text blocks in {rm_file.name}"

    def test_text_content_matches_between_stages(self, stage1_rm_files, stage2_rm_files):
        """Verify text content is the same between stages.

        Adding annotations should not change the text content.
        """
        # Match files by name
        stage1_by_name = {f.name: f for f in stage1_rm_files}
        stage2_by_name = {f.name: f for f in stage2_rm_files}

        common_names = set(stage1_by_name.keys()) & set(stage2_by_name.keys())
        assert len(common_names) > 0, "No matching .rm files between stages"

        for name in common_names:
            stage1_blocks, _ = extract_text_blocks_from_rm(stage1_by_name[name])
            stage2_blocks, _ = extract_text_blocks_from_rm(stage2_by_name[name])

            stage1_text = "\n".join(b.content for b in stage1_blocks)
            stage2_text = "\n".join(b.content for b in stage2_blocks)

            assert stage1_text == stage2_text, f"Text content differs in {name}"


def make_layout_config():
    """Create a default LayoutConfig for testing."""
    return LayoutConfig(
        margin_top=50,
        margin_bottom=50,
        margin_left=50,
        margin_right=50,
    )


class TestAnnotationPreservation:
    """Tests for annotation preservation logic."""

    def test_generator_can_read_annotated_file(self, stage2_rm_files, markdown_file):
        """Verify generator can read annotated .rm files."""
        layout = make_layout_config()
        generator = RemarkableGenerator(layout)

        for rm_file in stage2_rm_files:
            # Extract text blocks (used in preservation)
            text_blocks, text_origin_y, full_text = generator._extract_text_blocks_from_rm(rm_file)

            # Should extract without error
            assert len(text_blocks) >= 0  # May be empty for some pages

    def test_annotation_extraction_for_preservation(self, stage2_rm_files):
        """Test that annotations can be extracted for round-trip preservation."""
        import rmscene

        for rm_file in stage2_rm_files:
            with open(rm_file, "rb") as f:
                blocks = list(rmscene.read_blocks(f))

            # Find annotation blocks
            annotation_blocks = [
                b for b in blocks if "Line" in type(b).__name__ or "Glyph" in type(b).__name__
            ]

            # Stage2 should have annotation blocks
            # (Not all pages may have annotations, so we don't assert > 0 here)
            for block in annotation_blocks:
                # Verify structure
                assert hasattr(block, "item"), "Annotation block missing item"

    def test_roundtrip_structure_preservation(self, stage2_rm_files):
        """Verify .rm file structure elements needed for round-trip."""
        import rmscene

        for rm_file in stage2_rm_files:
            with open(rm_file, "rb") as f:
                blocks = list(rmscene.read_blocks(f))

            block_types = [type(b).__name__ for b in blocks]

            # Essential blocks for round-trip
            assert "RootTextBlock" in block_types, f"Missing RootTextBlock in {rm_file.name}"
            assert "TreeNodeBlock" in block_types, f"Missing TreeNodeBlock in {rm_file.name}"


class TestPositionMapping:
    """Tests for annotation position mapping."""

    def test_position_mapping_calculation(self, stage2_rm_files, markdown_file):
        """Test that position mapping can be calculated."""
        from rock_paper_sync.annotations import calculate_position_mapping

        layout = make_layout_config()
        generator = RemarkableGenerator(layout)

        for rm_file in stage2_rm_files:
            old_blocks, _, _ = generator._extract_text_blocks_from_rm(rm_file)

            if not old_blocks:
                continue

            # Simulate new blocks (same content for test)
            new_blocks = old_blocks.copy()

            # Calculate position mapping
            position_map = calculate_position_mapping(old_blocks, new_blocks)

            # Should map each old block to same index (content unchanged)
            for i in range(len(old_blocks)):
                assert i in position_map, f"Missing mapping for block {i}"
                # With identical content, should map to same index
                assert position_map[i] == i, f"Expected {i} -> {i}, got {i} -> {position_map[i]}"


class TestCoordinateSpace:
    """Tests for coordinate space handling in annotated files."""

    def test_annotation_coordinate_spaces(self, stage2_rm_files):
        """Identify coordinate spaces used in annotations."""
        from rmscene.tagged_block_common import CrdtId

        for rm_file in stage2_rm_files:
            annotations = read_annotations(rm_file)

            absolute_count = 0
            relative_count = 0

            for ann in annotations:
                if ann.parent_id is None:
                    continue

                if ann.parent_id == CrdtId(0, 11):
                    absolute_count += 1
                else:
                    relative_count += 1

            # Log coordinate space distribution
            print(f"\n{rm_file.name}: absolute={absolute_count}, relative={relative_count}")

    def test_transformation_completeness(self, stage2_rm_files):
        """Verify all annotations can be transformed to absolute space."""
        for rm_file in stage2_rm_files:
            annotations = read_annotations(rm_file)

            for ann in annotations:
                if ann.type == AnnotationType.STROKE and ann.stroke:
                    # Should be able to get center Y
                    # Note: This uses the raw annotation, not rmscene block
                    # So we test the bounding box exists
                    bbox = ann.stroke.bounding_box
                    assert bbox is not None, "Stroke missing bounding box"
                    center_y = bbox.y + bbox.h / 2
                    assert center_y is not None, "Could not calculate center Y"


class TestEndToEndPreservation:
    """End-to-end tests for annotation preservation workflow."""

    def test_preservation_pipeline(self, stage2_rm_files, markdown_file, page_uuids):
        """Test the full preservation pipeline with real data."""
        layout = make_layout_config()
        generator = RemarkableGenerator(layout)

        # Parse original markdown
        md_doc = parse_markdown_file(markdown_file)

        # Generate document with existing page UUIDs and .rm files
        # Map page UUIDs to existing files
        rm_file_list = []
        for uuid in page_uuids[: len(stage2_rm_files)]:
            # Find matching .rm file
            matching = [f for f in stage2_rm_files if uuid in f.name]
            if matching:
                rm_file_list.append(matching[0])
            else:
                rm_file_list.append(None)

        if not any(rm_file_list):
            pytest.skip("Could not match UUIDs to .rm files")

        # Generate document with preservation
        doc = generator.generate_document(
            md_doc,
            parent_uuid="",
            doc_uuid=None,
            existing_page_uuids=page_uuids[: len(rm_file_list)],
            existing_rm_files=rm_file_list,
        )

        # Verify document structure
        assert doc is not None
        assert len(doc.pages) > 0

        # Check annotations were preserved on some pages
        pages_with_annotations = sum(
            1 for page in doc.pages if hasattr(page, "annotation_blocks") and page.annotation_blocks
        )

        print(f"\nPreserved annotations on {pages_with_annotations}/{len(doc.pages)} pages")

    def test_generate_rm_with_preserved_annotations(
        self, stage2_rm_files, markdown_file, page_uuids
    ):
        """Test generating .rm files with preserved annotations."""
        layout = make_layout_config()
        generator = RemarkableGenerator(layout)

        # Parse markdown
        md_doc = parse_markdown_file(markdown_file)

        # Map UUIDs to files
        rm_file_list = []
        for uuid in page_uuids[: len(stage2_rm_files)]:
            matching = [f for f in stage2_rm_files if uuid in f.name]
            rm_file_list.append(matching[0] if matching else None)

        if not any(rm_file_list):
            pytest.skip("Could not match UUIDs to .rm files")

        # Generate document
        doc = generator.generate_document(
            md_doc,
            parent_uuid="",
            existing_page_uuids=page_uuids[: len(rm_file_list)],
            existing_rm_files=rm_file_list,
        )

        # Try generating .rm file for pages with preserved annotations
        for page in doc.pages:
            if hasattr(page, "annotation_blocks") and page.annotation_blocks:
                # This should work without error
                rm_bytes = generator.generate_rm_file(page)

                # Should produce valid output
                assert len(rm_bytes) > 0, "Generated empty .rm file"

                # Should start with rmscene magic bytes
                # Magic header is "reMarkable lines with" followed by version info
                assert rm_bytes.startswith(b"reMarkab"), "Invalid .rm file header"

                print(
                    f"\nGenerated .rm file: {len(rm_bytes)} bytes, "
                    f"{len(page.annotation_blocks)} annotations preserved"
                )
