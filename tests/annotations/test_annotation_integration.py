"""Integration tests for annotation preservation using real .rm files."""

from pathlib import Path

import pytest

from rock_paper_sync.annotations import AnnotationType, read_annotations

TESTDATA_DIR = Path(__file__).parent / "testdata" / "real_world_annotation_test"


class TestRealWorldAnnotationPreservation:
    """Test annotation preservation using real-world captured data."""

    @pytest.fixture
    def stage1_files(self):
        """Load stage 1 (initial, no annotations) files."""
        stage1_dir = TESTDATA_DIR / "stage1_initial"
        return {
            "dir": stage1_dir,
            "markdown": stage1_dir / "test1.md",
            "rm_files": list(stage1_dir.glob("*.rm")),
            "doc_uuid": (stage1_dir / "doc_uuid.txt").read_text().strip(),
            "page_uuids": (stage1_dir / "page_uuids.txt").read_text().strip().split("\n"),
        }

    @pytest.fixture
    def stage2_files(self):
        """Load stage 2 (annotated) files."""
        stage2_dir = TESTDATA_DIR / "stage2_annotated"
        if not stage2_dir.exists():
            pytest.skip("Stage 2 not yet captured")

        return {
            "dir": stage2_dir,
            "rm_files": list(stage2_dir.glob("*.rm")),
            "page_uuids": (stage2_dir / "page_uuids.txt").read_text().strip().split("\n"),
        }

    @pytest.fixture
    def stage3_files(self):
        """Load stage 3 (modified markdown, preserved annotations) files."""
        stage3_dir = TESTDATA_DIR / "stage3_modified"
        if not stage3_dir.exists():
            pytest.skip("Stage 3 not yet captured")

        return {
            "dir": stage3_dir,
            "markdown": stage3_dir / "test1_modified.md",
            "rm_files": list(stage3_dir.glob("*.rm")),
            "page_uuids": (stage3_dir / "page_uuids.txt").read_text().strip().split("\n"),
        }

    def test_stage1_has_no_annotations(self, stage1_files):
        """Verify initial state has no annotations."""
        total_annotations = 0
        for rm_file in stage1_files["rm_files"]:
            annotations = read_annotations(rm_file)
            total_annotations += len(annotations)

        assert total_annotations == 0

    def test_stage2_has_annotations(self, stage2_files):
        """Verify annotations were added in stage 2."""
        total_annotations = 0
        for rm_file in stage2_files["rm_files"]:
            annotations = read_annotations(rm_file)
            total_annotations += len(annotations)

        assert total_annotations > 0

    def test_annotations_preserved_after_modification(self, stage2_files, stage3_files):
        """Verify annotations are preserved when markdown is modified."""
        # Count annotations in stage 2
        stage2_count = sum(len(read_annotations(rm_file)) for rm_file in stage2_files["rm_files"])
        assert stage2_count > 0

        # Count annotations in stage 3
        stage3_count = sum(len(read_annotations(rm_file)) for rm_file in stage3_files["rm_files"])

        assert stage3_count == stage2_count

    def test_annotation_types_preserved(self, stage2_files, stage3_files):
        """Verify annotation types are preserved correctly."""

        def count_types(files):
            types = {"strokes": 0, "highlights": 0}
            for rm_file in files["rm_files"]:
                for ann in read_annotations(rm_file):
                    if ann.type == AnnotationType.STROKE:
                        types["strokes"] += 1
                    elif ann.type == AnnotationType.HIGHLIGHT:
                        types["highlights"] += 1
            return types

        stage2_types = count_types(stage2_files)
        stage3_types = count_types(stage3_files)

        assert stage3_types == stage2_types

    def test_stroke_details_preserved(self, stage2_files, stage3_files):
        """Verify stroke details are preserved."""

        def find_stroke(files):
            for rm_file in files["rm_files"]:
                for ann in read_annotations(rm_file):
                    if ann.type == AnnotationType.STROKE:
                        return ann.stroke
            return None

        stage2_stroke = find_stroke(stage2_files)
        if not stage2_stroke:
            pytest.skip("No strokes in stage 2")

        stage3_stroke = find_stroke(stage3_files)
        assert stage3_stroke is not None

        assert len(stage3_stroke.points) == len(stage2_stroke.points)
        assert stage3_stroke.color == stage2_stroke.color
        assert stage3_stroke.tool == stage2_stroke.tool
        assert stage3_stroke.thickness == stage2_stroke.thickness

    def test_markdown_content_changed(self, stage1_files, stage3_files):
        """Verify that markdown was actually modified between stages."""
        original_content = stage1_files["markdown"].read_text()
        modified_content = stage3_files["markdown"].read_text()

        assert original_content != modified_content
