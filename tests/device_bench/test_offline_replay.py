"""Offline replay tests using containerized rmfakecloud.

These tests run automatically without a physical reMarkable device.
They use podman/docker to start rmfakecloud and replay pre-collected testdata.

Usage:
    # Run all offline tests (starts rmfakecloud container automatically)
    uv run pytest tests/device_bench/test_offline_replay.py -v

    # Run with specific testdata
    uv run pytest tests/device_bench/test_offline_replay.py -v \\
        --test-artifact=ocr_handwriting_legacy

Requirements:
    - podman or docker available
    - Testdata available (run 'migrate-legacy' or 'collect' command)
"""

import io
import pytest
from pathlib import Path

from rmscene import read_blocks
from rmscene.scene_items import Pen, PenColor
from rock_paper_sync.annotations import read_annotations, AnnotationType


# Known test IDs from migrated/collected testdata
LEGACY_OCR_TEST_ID = "ocr_handwriting_legacy"


@pytest.mark.offline
class TestOfflineInfrastructure:
    """Tests for offline infrastructure (rmfakecloud, testdata store)."""

    def test_rmfakecloud_connection(self, rmfakecloud_service):
        """Verify rmfakecloud is running and accessible."""
        import requests

        resp = requests.get(f"{rmfakecloud_service}/health")
        assert resp.status_code == 200

    def test_testdata_store_accessible(self, testdata_store):
        """Verify testdata store is configured."""
        assert testdata_store.collected_dir.exists()
        assert testdata_store.curated_dir.exists()

    def test_legacy_testdata_available(self, testdata_store):
        """Verify migrated legacy testdata is available."""
        available = testdata_store.list_available_tests()
        test_ids = [m.test_id for m in available]

        assert LEGACY_OCR_TEST_ID in test_ids, (
            f"Legacy testdata not found. Run: "
            f"uv run python -m tests.device_bench.run_device_tests migrate-legacy"
        )


@pytest.mark.offline
class TestOCRHandwritingReplay:
    """Replay tests using the OCR handwriting testdata."""

    def test_load_ocr_handwriting_testdata(self, testdata_store):
        """Verify OCR handwriting testdata can be loaded."""
        artifacts = testdata_store.load_artifacts(LEGACY_OCR_TEST_ID)

        assert artifacts.manifest.test_id == LEGACY_OCR_TEST_ID
        assert len(artifacts.rm_files) == 2
        assert "OCR" in artifacts.manifest.description

    def test_ocr_testdata_has_source_markdown(self, testdata_store):
        """Verify source markdown is included."""
        artifacts = testdata_store.load_artifacts(LEGACY_OCR_TEST_ID)

        assert artifacts.source_markdown
        assert "OCR Test Document" in artifacts.source_markdown
        assert "hello" in artifacts.source_markdown  # Test case 1

    def test_ocr_testdata_rm_files_valid(self, testdata_store):
        """Verify .rm files are valid rmscene format."""
        import io
        from rmscene import read_blocks

        artifacts = testdata_store.load_artifacts(LEGACY_OCR_TEST_ID)

        for page_uuid, rm_data in artifacts.rm_files.items():
            # Should be able to parse as rmscene
            blocks = list(read_blocks(io.BytesIO(rm_data)))
            assert len(blocks) > 0, f"No blocks in {page_uuid}.rm"


@pytest.mark.offline
class TestOfflineDeviceEmulator:
    """Tests for the OfflineEmulator functionality."""

    def test_offline_device_has_cloud_url(self, offline_device):
        """Verify offline_device is configured with rmfakecloud URL."""
        assert offline_device.cloud_url.startswith("http://")
        assert "3001" in offline_device.cloud_url  # Test port (3001) to avoid conflict with real rmfakecloud

    def test_offline_device_can_load_testdata(self, offline_device, testdata_store):
        """Verify offline device can load testdata."""
        offline_device.load_test(LEGACY_OCR_TEST_ID)

        assert offline_device._current_artifacts is not None
        assert offline_device._current_artifacts.manifest.test_id == LEGACY_OCR_TEST_ID


# =============================================================================
# Pen Property Verification Tests
#
# These tests verify that pen colors, tools, and thickness are properly
# preserved through the collection and extraction pipeline.
#
# To collect testdata for these tests:
#   uv run python -m tests.device_bench.run_device_tests collect \
#       tests/device_bench/fixtures/pen_properties_baseline.md \
#       --test-id pen_colors_multicolor \
#       --description "Annotations with multiple pen colors"
# =============================================================================


@pytest.mark.offline
class TestPenPropertyExtraction:
    """Tests verifying pen properties are extracted from collected testdata."""

    def test_legacy_testdata_extracts_annotations(self, testdata_store):
        """Verify annotations can be extracted from legacy OCR testdata."""
        artifacts = testdata_store.load_artifacts(LEGACY_OCR_TEST_ID)

        all_annotations = []
        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            all_annotations.extend(annotations)

        assert len(all_annotations) > 0, "No annotations extracted from testdata"

    def test_legacy_testdata_has_stroke_colors(self, testdata_store):
        """Verify stroke colors are preserved in extracted annotations."""
        artifacts = testdata_store.load_artifacts(LEGACY_OCR_TEST_ID)

        colors_found = set()
        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    colors_found.add(ann.stroke.color)

        # Legacy OCR testdata uses black (PENCIL)
        assert PenColor.BLACK.value in colors_found

    def test_legacy_testdata_has_stroke_tools(self, testdata_store):
        """Verify stroke tools are preserved in extracted annotations."""
        artifacts = testdata_store.load_artifacts(LEGACY_OCR_TEST_ID)

        tools_found = set()
        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    tools_found.add(ann.stroke.tool)

        # Legacy OCR testdata uses BALLPOINT_2 (value=15)
        assert Pen.BALLPOINT_2.value in tools_found

    def test_legacy_testdata_has_thickness(self, testdata_store):
        """Verify stroke thickness is preserved in extracted annotations."""
        artifacts = testdata_store.load_artifacts(LEGACY_OCR_TEST_ID)

        thicknesses = []
        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    thicknesses.append(ann.stroke.thickness)

        assert len(thicknesses) > 0
        assert all(t > 0 for t in thicknesses)


@pytest.mark.offline
class TestPenColorsMulticolor:
    """Tests for multicolor pen testdata (once collected).

    Collect with:
        uv run python -m tests.device_bench.run_device_tests collect \\
            tests/device_bench/fixtures/pen_properties_baseline.md \\
            --test-id pen_colors_multicolor \\
            --description "Section 1: Write 'hello' in black, red, blue, green, yellow"
    """

    TEST_ID = "pen_colors_multicolor"

    def test_multicolor_testdata_available(self, testdata_store):
        """Check if multicolor testdata has been collected."""
        available = testdata_store.list_available_tests()
        test_ids = [m.test_id for m in available]

        if self.TEST_ID not in test_ids:
            pytest.skip(
                f"Testdata '{self.TEST_ID}' not collected. Run:\n"
                f"  uv run python -m tests.device_bench.run_device_tests collect \\\n"
                f"      tests/device_bench/fixtures/pen_properties_baseline.md \\\n"
                f"      --test-id {self.TEST_ID}"
            )

    def test_multicolor_has_distinct_colors(self, testdata_store):
        """Verify multiple distinct colors are present."""
        available = testdata_store.list_available_tests()
        if self.TEST_ID not in [m.test_id for m in available]:
            pytest.skip(f"Testdata '{self.TEST_ID}' not collected")

        artifacts = testdata_store.load_artifacts(self.TEST_ID)

        colors_found = set()
        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    colors_found.add(ann.stroke.color)

        # Should have multiple colors (black, red, blue, green, yellow)
        assert len(colors_found) >= 3, f"Expected multiple colors, found: {colors_found}"


@pytest.mark.offline
class TestPenToolsVariety:
    """Tests for pen tool variety.

    Reuses pen_colors_multicolor testdata which contains 6+ different tools.
    """

    TEST_ID = "pen_colors_multicolor"

    def test_tools_variety_has_distinct_tools(self, testdata_store):
        """Verify multiple distinct pen tools are present."""
        available = testdata_store.list_available_tests()
        if self.TEST_ID not in [m.test_id for m in available]:
            pytest.skip(f"Testdata '{self.TEST_ID}' not collected")

        artifacts = testdata_store.load_artifacts(self.TEST_ID)

        tools_found = set()
        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    tools_found.add(ann.stroke.tool)

        # Should have multiple tools
        assert len(tools_found) >= 3, f"Expected at least 3 tools, found {len(tools_found)}: {tools_found}"

    def test_tool_names_extracted(self, testdata_store):
        """Verify tool names can be mapped from enum values."""
        available = testdata_store.list_available_tests()
        if self.TEST_ID not in [m.test_id for m in available]:
            pytest.skip(f"Testdata '{self.TEST_ID}' not collected")

        artifacts = testdata_store.load_artifacts(self.TEST_ID)

        tools_found = set()
        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    tools_found.add(ann.stroke.tool)

        # Map to names
        tool_names = [t.name for t in Pen if t.value in tools_found]
        assert len(tool_names) >= 3, f"Expected at least 3 tool names, found: {tool_names}"


@pytest.mark.offline
class TestHighlightColors:
    """Tests for highlight extraction.

    Reuses pen_colors_multicolor testdata which contains highlights.
    Note: rmscene may normalize all highlight colors to HIGHLIGHT (9).
    """

    TEST_ID = "pen_colors_multicolor"

    def test_highlights_extracted(self, testdata_store):
        """Verify highlights are extracted from testdata."""
        available = testdata_store.list_available_tests()
        if self.TEST_ID not in [m.test_id for m in available]:
            pytest.skip(f"Testdata '{self.TEST_ID}' not collected")

        artifacts = testdata_store.load_artifacts(self.TEST_ID)

        highlight_count = 0
        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.HIGHLIGHT:
                    highlight_count += 1

        # Should have at least some highlights
        assert highlight_count >= 1, f"Expected highlights, found: {highlight_count}"

    def test_highlight_has_text_content(self, testdata_store):
        """Verify highlights contain text content."""
        available = testdata_store.list_available_tests()
        if self.TEST_ID not in [m.test_id for m in available]:
            pytest.skip(f"Testdata '{self.TEST_ID}' not collected")

        artifacts = testdata_store.load_artifacts(self.TEST_ID)

        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.HIGHLIGHT:
                    # Highlight should have text and rectangles
                    assert hasattr(ann.highlight, 'text')
                    assert hasattr(ann.highlight, 'rectangles')
                    assert len(ann.highlight.rectangles) >= 1


@pytest.mark.offline
class TestPenThicknessVariation:
    """Tests for pen thickness variation.

    Reuses pen_colors_multicolor testdata which contains multiple thickness values.
    """

    TEST_ID = "pen_colors_multicolor"

    def test_thickness_has_distinct_values(self, testdata_store):
        """Verify multiple distinct thickness values are present."""
        available = testdata_store.list_available_tests()
        if self.TEST_ID not in [m.test_id for m in available]:
            pytest.skip(f"Testdata '{self.TEST_ID}' not collected")

        artifacts = testdata_store.load_artifacts(self.TEST_ID)

        thicknesses_found = set()
        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    thicknesses_found.add(ann.stroke.thickness)

        # Should have multiple thickness values
        assert len(thicknesses_found) >= 2, f"Expected at least 2 thickness values, found {len(thicknesses_found)}: {sorted(thicknesses_found)}"

    def test_thickness_values_are_positive(self, testdata_store):
        """Verify all thickness values are positive floats."""
        available = testdata_store.list_available_tests()
        if self.TEST_ID not in [m.test_id for m in available]:
            pytest.skip(f"Testdata '{self.TEST_ID}' not collected")

        artifacts = testdata_store.load_artifacts(self.TEST_ID)

        for page_uuid, rm_data in artifacts.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    assert ann.stroke.thickness > 0, f"Thickness should be positive, got: {ann.stroke.thickness}"
                    assert isinstance(ann.stroke.thickness, float), f"Thickness should be float, got: {type(ann.stroke.thickness)}"
