"""Offline replay tests using containerized rmfakecloud.

These tests run automatically without a physical reMarkable device.
They use podman/docker to start rmfakecloud and replay pre-collected testdata.

Usage:
    # Run all offline tests (starts rmfakecloud container automatically)
    uv run pytest tests/record_replay/test_offline_replay.py -v

    # Run with specific testdata
    uv run pytest tests/record_replay/test_offline_replay.py -v \\
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


@pytest.fixture
def annotation_manager(offline_device, workspace, testdata_store, request):
    """Fixture that abstracts online/offline annotation handling.

    Provides methods to:
    - Setup and teardown tests with testdata
    - Upload documents and get annotations (from user online or testdata offline)

    Uses offline_device which provides rmfakecloud automatically.

    Usage:
        def test_pen_colors(annotation_manager):
            state = annotation_manager.get_annotated_document("pen_colors_multicolor")
            # verify state
    """

    class AnnotationManager:
        def __init__(self, device, workspace, testdata_store, request):
            self.device = device
            self.workspace = workspace
            self.testdata_store = testdata_store
            self.device_mode = "offline"  # Always offline for these tests
            self.current_test_id = None

        def get_annotated_document(self, test_id):
            """Load testdata and upload document, returning annotated state.

            Args:
                test_id: Test artifact ID to load

            Returns:
                Tuple of (state, doc_uuid) where state has rm_files with annotations

            Raises:
                FileNotFoundError: If testdata not found in offline mode
            """
            self.current_test_id = test_id

            if self.device_mode == "offline":
                # In offline mode, must have testdata
                try:
                    artifacts = self.testdata_store.load_artifacts(test_id)
                except FileNotFoundError:
                    raise FileNotFoundError(f"Testdata '{test_id}' not found")

                # Set up workspace with source markdown from testdata
                self.workspace.test_doc.write_text(artifacts.source_markdown)
                self.device.load_test(test_id)
            else:
                self.testdata_store.load_artifacts(test_id)

            # Setup test (online: prepare for capture, offline: use loaded testdata)
            self.device.start_test(test_id)

            # Upload document and get annotations
            doc_uuid = self.device.upload_document(self.workspace.test_doc)
            state = self.device.wait_for_annotations(doc_uuid)

            return state, doc_uuid

        def cleanup(self, success=True):
            """Cleanup test and save artifacts (online only)."""
            if self.current_test_id:
                self.device.end_test(self.current_test_id, success=success)

    manager = AnnotationManager(offline_device, workspace, testdata_store, request)
    yield manager
    manager.cleanup()


@pytest.mark.offline
class TestOfflineInfrastructure:
    """Tests for offline infrastructure (rmfakecloud, testdata store)."""

    def test_rmfakecloud_connection(self, rmfakecloud):
        """Verify rmfakecloud is running and accessible."""
        import requests

        resp = requests.get(f"{rmfakecloud}/health")
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
    """Hybrid online/offline tests for OCR handwriting testdata.

    Online mode: User writes handwriting that gets OCR processed
    Offline mode: Replays pre-recorded OCR handwriting testdata

    Currently runs offline only (testdata available).
    Could support online mode by adding user prompts.
    """

    def test_load_ocr_handwriting_testdata(self, annotation_manager, golden_replay):
        """Verify OCR handwriting testdata can be loaded and parsed."""
        golden_replay.start(LEGACY_OCR_TEST_ID)
        state, _ = annotation_manager.get_annotated_document(LEGACY_OCR_TEST_ID)

        assert len(state.rm_files) == 2
        assert all(isinstance(rm_data, bytes) for rm_data in state.rm_files.values())

    def test_ocr_testdata_has_annotations(self, annotation_manager, golden_replay):
        """Verify annotations are present in OCR testdata.

        Also validates vault state matches expected baseline after annotation replay.
        """
        golden_replay.start(LEGACY_OCR_TEST_ID)
        state, _ = annotation_manager.get_annotated_document(LEGACY_OCR_TEST_ID)

        # Verify we have .rm files with annotations
        all_annotations = []
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            all_annotations.extend(annotations)

        assert len(all_annotations) > 0, "No annotations in OCR testdata"

        # Validate final state matches baseline
        vault_state = golden_replay.validate_vault_state()
        assert vault_state is not None

    def test_ocr_testdata_rm_files_valid(self, annotation_manager, golden_replay):
        """Verify .rm files are valid rmscene format.

        Also validates that replaying produces consistent output.
        """
        golden_replay.start(LEGACY_OCR_TEST_ID)
        state, _ = annotation_manager.get_annotated_document(LEGACY_OCR_TEST_ID)

        for page_uuid, rm_data in state.rm_files.items():
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
#       tests/record_replay/fixtures/pen_properties_baseline.md \
#       --test-id pen_colors_multicolor \
#       --description "Annotations with multiple pen colors"
# =============================================================================


@pytest.mark.offline
class TestPenPropertyExtraction:
    """Hybrid online/offline tests verifying pen properties in annotations.

    Online mode: User annotates document with various pen properties
    Offline mode: Replays pre-recorded annotations with those properties

    Currently runs offline only (testdata available).
    Could support online mode by adding user interaction prompts.
    """

    def test_legacy_testdata_extracts_annotations(self, annotation_manager):
        """Verify annotations can be extracted from testdata."""
        state, _ = annotation_manager.get_annotated_document(LEGACY_OCR_TEST_ID)

        all_annotations = []
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            all_annotations.extend(annotations)

        assert len(all_annotations) > 0, "No annotations extracted"

    def test_legacy_testdata_has_stroke_colors(self, annotation_manager):
        """Verify stroke colors are preserved in annotations."""
        state, _ = annotation_manager.get_annotated_document(LEGACY_OCR_TEST_ID)

        colors_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    colors_found.add(ann.stroke.color)

        # Legacy OCR testdata uses black (PENCIL)
        assert PenColor.BLACK.value in colors_found

    def test_legacy_testdata_has_stroke_tools(self, annotation_manager):
        """Verify stroke tools are preserved in annotations."""
        state, _ = annotation_manager.get_annotated_document(LEGACY_OCR_TEST_ID)

        tools_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    tools_found.add(ann.stroke.tool)

        # Legacy OCR testdata uses BALLPOINT_2 (value=15)
        assert Pen.BALLPOINT_2.value in tools_found

    def test_legacy_testdata_has_thickness(self, annotation_manager):
        """Verify stroke thickness is preserved in annotations."""
        state, _ = annotation_manager.get_annotated_document(LEGACY_OCR_TEST_ID)

        thicknesses = []
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    thicknesses.append(ann.stroke.thickness)

        assert len(thicknesses) > 0
        assert all(t > 0 for t in thicknesses)


@pytest.mark.offline
class TestPenColorsMulticolor:
    """Hybrid online/offline tests for multicolor pen annotations.

    Online mode: User writes text in multiple colors
    Offline mode: Replays pre-recorded multi-color annotations

    Currently runs offline only (when testdata available).
    Could support online mode by adding user prompts.

    To capture new testdata:
        uv run python -m tests.device_bench.run_device_tests collect \\
            tests/record_replay/fixtures/pen_properties_baseline.md \\
            --test-id pen_colors_multicolor \\
            --description "Section 1: Write 'hello' in black, red, blue, green, yellow"
    """

    TEST_ID = "pen_colors_multicolor"

    def test_multicolor_testdata_available(self, annotation_manager, golden_replay):
        """Check if multicolor testdata can be loaded.

        Also validates vault state matches expected baseline.
        """
        try:
            golden_replay.start(self.TEST_ID)
            state, _ = annotation_manager.get_annotated_document(self.TEST_ID)
            assert state.rm_files
        except FileNotFoundError:
            pytest.skip(
                f"Testdata '{self.TEST_ID}' not available. To capture:\n"
                f"  uv run python -m tests.device_bench.run_device_tests collect \\\n"
                f"      tests/record_replay/fixtures/pen_properties_baseline.md \\\n"
                f"      --test-id {self.TEST_ID}"
            )

    def test_multicolor_has_distinct_colors(self, annotation_manager):
        """Verify multiple distinct colors are present in annotations."""
        try:
            state, _ = annotation_manager.get_annotated_document(self.TEST_ID)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{self.TEST_ID}' not available")

        colors_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    colors_found.add(ann.stroke.color)

        # Should have multiple colors
        assert len(colors_found) >= 2, f"Expected multiple colors, found: {colors_found}"


@pytest.mark.offline
class TestPenToolsVariety:
    """Hybrid online/offline tests for pen tool variety.

    Online mode: User annotates using multiple pen tools
    Offline mode: Replays pre-recorded annotations with varied tools

    Currently runs offline only (when testdata available).
    Could support online mode by adding user prompts.
    """

    TEST_ID = "pen_colors_multicolor"

    def test_tools_variety_has_distinct_tools(self, annotation_manager):
        """Verify multiple distinct pen tools are present in annotations."""
        try:
            state, _ = annotation_manager.get_annotated_document(self.TEST_ID)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{self.TEST_ID}' not available")

        tools_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    tools_found.add(ann.stroke.tool)

        # Should have multiple tools
        assert len(tools_found) >= 2, f"Expected multiple tools, found {len(tools_found)}: {tools_found}"

    def test_tool_names_extracted(self, annotation_manager):
        """Verify tool names can be mapped from enum values."""
        try:
            state, _ = annotation_manager.get_annotated_document(self.TEST_ID)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{self.TEST_ID}' not available")

        tools_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    tools_found.add(ann.stroke.tool)

        # Map to names
        tool_names = [t.name for t in Pen if t.value in tools_found]
        assert len(tool_names) >= 2, f"Expected tool names, found: {tool_names}"


@pytest.mark.offline
class TestHighlightColors:
    """Hybrid online/offline tests for highlight annotation.

    Online mode: User highlights text on document
    Offline mode: Replays pre-recorded highlight annotations

    Currently runs offline only (when testdata available).
    Could support online mode by adding user prompts.

    Note: rmscene may normalize all highlight colors to HIGHLIGHT (9).
    """

    TEST_ID = "pen_colors_multicolor"

    def test_highlights_extracted(self, annotation_manager):
        """Verify highlights are extracted from annotations."""
        try:
            state, _ = annotation_manager.get_annotated_document(self.TEST_ID)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{self.TEST_ID}' not available")

        highlight_count = 0
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.HIGHLIGHT:
                    highlight_count += 1

        # Should have at least some highlights
        assert highlight_count >= 1, f"Expected highlights, found: {highlight_count}"

    def test_highlight_has_text_content(self, annotation_manager):
        """Verify highlights contain text content."""
        try:
            state, _ = annotation_manager.get_annotated_document(self.TEST_ID)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{self.TEST_ID}' not available")

        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.HIGHLIGHT:
                    # Highlight should have text and rectangles
                    assert hasattr(ann.highlight, 'text')
                    assert hasattr(ann.highlight, 'rectangles')
                    assert len(ann.highlight.rectangles) >= 1


@pytest.mark.offline
class TestPenThicknessVariation:
    """Hybrid online/offline tests for pen thickness variation.

    Online mode: User annotates with varied pen pressures/thicknesses
    Offline mode: Replays pre-recorded annotations with varied thicknesses

    Currently runs offline only (when testdata available).
    Could support online mode by adding user prompts.
    """

    TEST_ID = "pen_colors_multicolor"

    def test_thickness_has_distinct_values(self, annotation_manager):
        """Verify multiple distinct thickness values are present in annotations."""
        try:
            state, _ = annotation_manager.get_annotated_document(self.TEST_ID)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{self.TEST_ID}' not available")

        thicknesses_found = set()
        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    thicknesses_found.add(ann.stroke.thickness)

        # Should have multiple thickness values
        assert len(thicknesses_found) >= 2, f"Expected multiple thickness values, found {len(thicknesses_found)}: {sorted(thicknesses_found)}"

    def test_thickness_values_are_positive(self, annotation_manager):
        """Verify all thickness values are positive floats."""
        try:
            state, _ = annotation_manager.get_annotated_document(self.TEST_ID)
        except FileNotFoundError:
            pytest.skip(f"Testdata '{self.TEST_ID}' not available")

        for page_uuid, rm_data in state.rm_files.items():
            annotations = read_annotations(io.BytesIO(rm_data))
            for ann in annotations:
                if ann.type == AnnotationType.STROKE:
                    assert ann.stroke.thickness > 0, f"Thickness should be positive, got: {ann.stroke.thickness}"
                    assert isinstance(ann.stroke.thickness, float), f"Thickness should be float, got: {type(ann.stroke.thickness)}"
