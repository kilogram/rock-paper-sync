"""Device calibration tests.

This module contains both:
- **Online tests** (`online_only`): Interactive workflow to capture calibration data from device
- **Offline tests**: Validate our layout engine against captured calibration profiles

Usage:
    # Capture calibration data for Paper Pro Move (interactive):
    uv run pytest "tests/record_replay/test_calibration.py::TestCalibrationCapture[paper_pro_move]" --online -s

    # Or use -k:
    uv run pytest tests/record_replay/test_calibration.py -k "Capture and paper_pro_move" --online -s

    # Run offline validation tests:
    uv run pytest tests/record_replay/test_calibration.py -v

    # List calibrated devices:
    ls tests/record_replay/testdata/calibration/

Calibration data is stored per-device at:
    tests/record_replay/testdata/calibration/{device_name}/
        profile.json         - Device calibration parameters
        calibration_*.rm     - Golden .rm files with highlights
"""

import json
import shutil
from enum import Enum
from pathlib import Path

import pytest

from rock_paper_sync.layout.device import PAPER_PRO_MOVE
from rock_paper_sync.layout.engine import WordWrapLayoutEngine

# =============================================================================
# Device Enumeration
# =============================================================================


class CalibrableDevice(str, Enum):
    """Supported reMarkable devices for calibration.

    Add new devices here as they become available for testing.
    """

    PAPER_PRO_MOVE = "paper_pro_move"
    # Future devices:
    # PAPER_PRO = "paper_pro"
    # RM2 = "rm2"


# =============================================================================
# Configuration
# =============================================================================

CALIBRATION_ROOT = Path(__file__).parent / "testdata" / "calibration"
CALIBRATION_FIXTURES = Path(__file__).parent / "fixtures"
CALIBRATION_DOCS = [
    "calibration_font_sizes.md",
    "calibration_chars.md",
    "calibration_wrap.md",
    "calibration_structure.md",
]


def get_device_dir(device: CalibrableDevice) -> Path:
    """Get the calibration data directory for a device."""
    return CALIBRATION_ROOT / device.value


def load_profile(device: CalibrableDevice) -> dict | None:
    """Load device profile from JSON file."""
    profile_path = get_device_dir(device) / "profile.json"
    if profile_path.exists():
        return json.loads(profile_path.read_text())
    return None


def ensure_calibration_docs_exist():
    """Verify calibration documents exist in fixtures."""
    missing = []
    for doc in CALIBRATION_DOCS:
        if not (CALIBRATION_FIXTURES / doc).exists():
            missing.append(doc)
    if missing:
        raise FileNotFoundError(
            f"Missing calibration documents: {missing}\n"
            f"Expected at: {CALIBRATION_FIXTURES}"
        )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def layout_engine():
    """Create layout engine for testing."""
    return WordWrapLayoutEngine.from_geometry(PAPER_PRO_MOVE, use_font_metrics=True)


# =============================================================================
# Online Tests: Calibration Capture
# =============================================================================


@pytest.mark.online_only
class TestCalibrationCapture:
    """Interactive calibration tests for device golden capture."""

    @pytest.mark.parametrize("device", list(CalibrableDevice), ids=lambda d: d.value)
    def test_capture_calibration(
        self,
        device: CalibrableDevice,
        workspace,
        bench,
    ):
        """Capture calibration data from device.

        This test:
        1. Syncs calibration documents to the device
        2. Prompts user to add highlights to marker characters
        3. Downloads annotated .rm files
        4. Extracts profile.json from the annotations
        5. Saves everything to the device-specific calibration directory
        """
        device_dir = get_device_dir(device)
        bench.info(f"Calibrating for device: {device.value}")
        bench.info(f"Calibration data will be saved to: {device_dir}")

        # Step 1: Verify calibration documents exist
        bench.info("Step 1: Verifying calibration documents")
        ensure_calibration_docs_exist()
        bench.ok(f"Found {len(CALIBRATION_DOCS)} calibration documents")

        # Step 2: Copy calibration docs to workspace vault
        bench.info("Step 2: Setting up calibration documents in workspace")
        vault_dir = workspace.workspace_dir
        for doc in CALIBRATION_DOCS:
            src = CALIBRATION_FIXTURES / doc
            dst = vault_dir / doc
            shutil.copy(src, dst)
            bench.ok(f"Copied {doc}")

        # Step 3: Sync to device
        bench.info("Step 3: Syncing calibration documents to device")
        workspace.run_sync("Syncing calibration documents")
        bench.ok("Sync complete")

        # Step 3.5: Upload geometry calibration with ruler strokes directly to cloud
        bench.info("Step 3.5: Uploading geometry calibration with ruler strokes")
        import uuid as uuid_module

        from rock_paper_sync.config import load_config
        from rock_paper_sync.converter import SyncEngine
        from rock_paper_sync.state import StateManager

        # Track geometry UUID for cleanup
        geometry_uuid = None

        # Load pre-generated .rm file with strokes
        pregenerated_rm = CALIBRATION_FIXTURES / "calibration_geometry.rm"
        if pregenerated_rm.exists():
            rm_bytes = pregenerated_rm.read_bytes()
            bench.info(f"Loaded pre-generated .rm file: {len(rm_bytes)} bytes")

            # Get cloud_sync from existing sync infrastructure
            config = load_config(workspace.config_file)
            state = StateManager(config.sync.state_database)
            engine = SyncEngine(config, state)

            # Generate new UUIDs for this document
            doc_uuid = str(uuid_module.uuid4())
            page_uuid = str(uuid_module.uuid4())
            geometry_uuid = doc_uuid  # Track for cleanup

            # Get the vault folder UUID (if configured)
            parent_uuid = ""
            if config.sync.vaults:
                vault = config.sync.vaults[0]  # Use first vault
                if vault.remarkable_folder:
                    # Get or create vault root folder
                    folder_uuid = state.get_folder_uuid(vault.name, "")
                    if folder_uuid:
                        parent_uuid = folder_uuid
                        bench.info(f"Placing in vault folder: {vault.remarkable_folder}")
                    else:
                        bench.info("Vault folder not found, placing in root")

            bench.info(f"Creating new document: {doc_uuid}")

            # Upload to the vault folder (or root if no vault configured)
            engine.cloud_sync.upload_document(
                doc_uuid=doc_uuid,
                document_name="calibration_geometry",
                pages=[(page_uuid, rm_bytes)],
                parent_uuid=parent_uuid,
                broadcast=True,
            )

            bench.ok(f"Geometry calibration uploaded (UUID: {doc_uuid})")
            bench.info("Device should receive it within a few seconds. If not, try manually syncing.")
        else:
            bench.warn("Pre-generated .rm file not found, skipping geometry calibration")

        # Step 4: Interactive prompt for user to add highlights
        bench.info("Step 4: Waiting for user to add highlights")
        print("\n" + "=" * 70)
        print("CALIBRATION INSTRUCTIONS")
        print("=" * 70)
        print(f"\nDevice: {device.value}")
        print("\nThe following documents have been synced to your device:")
        for doc in CALIBRATION_DOCS:
            print(f"  - {doc}")
        print("\nPlease perform these steps on your reMarkable device:")
        print()
        print("1. Open each calibration document")
        print("2. Use the highlighter tool to highlight each marker character:")
        print("   - calibration_chars.md: Highlight letters/numbers (single chars)")
        print("   - calibration_wrap.md: Highlight 'e' at start of each line")
        print("   - calibration_structure.md: Highlight each 'e' character")
        print("   - calibration_geometry.md: Has ruler strokes - compare to physical ruler!")
        print()
        print("3. Let the device sync your annotations to the cloud")
        print("   (Wait for the cloud sync icon to show completion)")
        print()
        print("=" * 70)

        input("\nPress ENTER when you have finished adding highlights and synced...")

        # Step 5: Download annotated files via sync
        bench.info("Step 5: Downloading annotated documents from cloud")
        workspace.run_sync("Downloading annotations")

        # Create device calibration directory
        device_dir.mkdir(parents=True, exist_ok=True)
        rm_files_dir = device_dir / "rm_files"
        rm_files_dir.mkdir(exist_ok=True)

        # Get document UUIDs from state database
        import sqlite3

        db_path = workspace.state_dir / "state.db"
        if not db_path.exists():
            pytest.fail("State database not found after sync")

        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT obsidian_path, remarkable_uuid FROM sync_state "
            "WHERE vault_name = 'device-bench'"
        )
        doc_mappings = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

        bench.ok(f"Found {len(doc_mappings)} synced documents")

        # Copy .rm files from cache to calibration directory
        cache_dir = workspace.cache_dir / "annotations"
        files_copied = 0

        for doc_name in [d.replace(".md", "") for d in CALIBRATION_DOCS]:
            obsidian_path = f"{doc_name}.md"
            doc_uuid = doc_mappings.get(obsidian_path)

            if not doc_uuid:
                bench.warn(f"No UUID found for {obsidian_path}")
                continue

            doc_cache = cache_dir / doc_uuid
            if not doc_cache.exists():
                bench.warn(f"No cached annotations for {doc_name} ({doc_uuid})")
                continue

            # Copy all .rm files for this document
            for rm_file in doc_cache.glob("*.rm"):
                dest_name = f"{doc_name}_{rm_file.stem}.rm"
                dest_path = rm_files_dir / dest_name
                shutil.copy(rm_file, dest_path)
                bench.ok(f"Copied {dest_name}")
                files_copied += 1

                # Also save as main calibration file (first page)
                main_file = device_dir / f"{doc_name}.rm"
                if not main_file.exists():
                    shutil.copy(rm_file, main_file)

        if files_copied == 0:
            bench.warn("No .rm files found in cache!")
            bench.info(f"Cache dir: {cache_dir}")
            if cache_dir.exists():
                bench.info(f"Cache contents: {list(cache_dir.rglob('*'))}")
            pytest.fail("No annotation files downloaded")

        # Step 6: Extract profile
        bench.info("Step 6: Extracting calibration profile")

        try:
            import sys

            tools_path = Path(__file__).parent.parent.parent / "tools"
            if str(tools_path) not in sys.path:
                sys.path.insert(0, str(tools_path))

            from calibration.extract_profile import extract_profile_from_directory

            profile = extract_profile_from_directory(device_dir, device.value)

            profile_path = device_dir / "profile.json"
            profile_path.write_text(json.dumps(profile, indent=2))
            bench.ok(f"Saved profile to {profile_path}")

            print("\n" + "=" * 70)
            print("CALIBRATION PROFILE")
            print("=" * 70)
            print(json.dumps(profile, indent=2))
            print("=" * 70)

        except ImportError as e:
            bench.warn(f"Could not import extract_profile tool: {e}")
            bench.info("Creating minimal profile...")

            from datetime import datetime

            profile = {
                "device_name": device.value,
                "page_width": 1404,
                "page_height": 1872,
                "text_width": 750.0,
                "layout_text_width": 758.0,
                "font_point_size": 29.5,
                "line_height": 57.0,
                "calibration_date": datetime.now().isoformat()[:10],
                "golden_files": [f.name for f in device_dir.glob("*.rm")],
            }
            profile_path = device_dir / "profile.json"
            profile_path.write_text(json.dumps(profile, indent=2))
            bench.ok(f"Saved minimal profile to {profile_path}")

        # Step 7: Summary
        print("\n" + "=" * 70)
        print("CALIBRATION COMPLETE")
        print("=" * 70)
        print(f"\nDevice: {device.value}")
        print(f"Data saved to: {device_dir}")
        print("\nFiles created:")
        for f in sorted(device_dir.rglob("*")):
            if f.is_file():
                rel = f.relative_to(device_dir)
                print(f"  {rel}")
        print("\nNext steps:")
        print("1. Run calibration validation tests:")
        print("   uv run pytest tests/record_replay/test_calibration.py -v")
        print("2. If tests fail, adjust layout parameters in device.py")
        print("=" * 70)

        bench.ok("Calibration workflow complete!")

        # Cleanup: Delete geometry calibration from cloud
        if geometry_uuid:
            try:
                bench.info("Cleaning up: Deleting geometry calibration from cloud")
                config = load_config(workspace.config_file)
                state = StateManager(config.sync.state_database)
                engine = SyncEngine(config, state)
                engine.cloud_sync.delete_document(geometry_uuid)
                bench.ok(f"Deleted geometry calibration ({geometry_uuid})")
            except Exception as e:
                bench.warn(f"Failed to cleanup geometry calibration: {e}")


# =============================================================================
# Offline Tests: Profile Validation
# =============================================================================


class TestProfileValidation:
    """Validate calibration profiles exist and have required fields."""

    @pytest.mark.parametrize("device", list(CalibrableDevice), ids=lambda d: d.value)
    def test_profile_exists(self, device: CalibrableDevice):
        """Device profile should exist after calibration."""
        profile = load_profile(device)
        if profile is None:
            pytest.skip(f"No calibration data for {device.value} yet")
        assert profile is not None

    @pytest.mark.parametrize("device", list(CalibrableDevice), ids=lambda d: d.value)
    def test_profile_has_required_fields(self, device: CalibrableDevice):
        """Profile should have all required fields."""
        profile = load_profile(device)
        if profile is None:
            pytest.skip(f"No calibration data for {device.value}")

        required_fields = [
            "device_name",
            "page_width",
            "page_height",
            "text_width",
            "layout_text_width",
            "font_point_size",
            "line_height",
        ]
        for field in required_fields:
            assert field in profile, f"Profile missing required field: {field}"

    @pytest.mark.parametrize("device", list(CalibrableDevice), ids=lambda d: d.value)
    def test_profile_has_structural_section(self, device: CalibrableDevice):
        """Profile should have structural layout parameters."""
        profile = load_profile(device)
        if profile is None:
            pytest.skip(f"No calibration data for {device.value}")

        if "structural" not in profile:
            pytest.skip("Profile missing 'structural' section (optional)")

        structural = profile["structural"]
        structural_fields = [
            "paragraph_spacing_px",
            "bullet_item_spacing_px",
            "list_item_spacing_px",
        ]
        for field in structural_fields:
            assert field in structural, f"Structural section missing: {field}"


# =============================================================================
# Offline Tests: Engine Calibration Accuracy
# =============================================================================


class TestEngineCalibration:
    """Validate our layout engine matches captured calibration data."""

    @pytest.mark.parametrize("device", list(CalibrableDevice), ids=lambda d: d.value)
    def test_line_height_matches_profile(self, device: CalibrableDevice, layout_engine):
        """Engine line height should match device profile."""
        profile = load_profile(device)
        if profile is None:
            pytest.skip(f"No calibration data for {device.value}")

        profile_line_height = profile["line_height"]
        engine_line_height = layout_engine.line_height

        assert abs(profile_line_height - engine_line_height) < 1.0, (
            f"Line height mismatch: profile={profile_line_height}, "
            f"engine={engine_line_height}"
        )

    @pytest.mark.parametrize("device", list(CalibrableDevice), ids=lambda d: d.value)
    def test_layout_width_matches_profile(self, device: CalibrableDevice, layout_engine):
        """Engine layout width should match device profile."""
        profile = load_profile(device)
        if profile is None:
            pytest.skip(f"No calibration data for {device.value}")

        profile_width = profile["layout_text_width"]
        engine_width = layout_engine.text_width

        assert abs(profile_width - engine_width) < 5.0, (
            f"Layout width mismatch: profile={profile_width}, engine={engine_width}"
        )


# =============================================================================
# Offline Tests: Structural Spacing
# =============================================================================


class TestStructuralSpacing:
    """Validate structural element spacing is reasonable."""

    @pytest.mark.parametrize("device", list(CalibrableDevice), ids=lambda d: d.value)
    def test_paragraph_spacing_reasonable(self, device: CalibrableDevice):
        """Paragraph spacing should be close to line height."""
        profile = load_profile(device)
        if profile is None:
            pytest.skip(f"No calibration data for {device.value}")

        structural = profile.get("structural", {})
        paragraph_spacing = structural.get("paragraph_spacing_px", 57.0)
        line_height = profile.get("line_height", 57.0)

        # Paragraph spacing is typically 1-2x line height
        assert 0.5 * line_height <= paragraph_spacing <= 3.0 * line_height, (
            f"Paragraph spacing {paragraph_spacing} outside expected range "
            f"for line height {line_height}"
        )

    @pytest.mark.parametrize("device", list(CalibrableDevice), ids=lambda d: d.value)
    def test_list_indent_reasonable(self, device: CalibrableDevice):
        """List indentation should be positive and reasonable."""
        profile = load_profile(device)
        if profile is None:
            pytest.skip(f"No calibration data for {device.value}")

        structural = profile.get("structural", {})
        bullet_indent = structural.get("bullet_indent_px", 30.0)
        list_indent = structural.get("list_indent_px", 30.0)

        assert 10.0 <= bullet_indent <= 100.0, (
            f"Bullet indent {bullet_indent} outside expected range"
        )
        assert 10.0 <= list_indent <= 100.0, (
            f"List indent {list_indent} outside expected range"
        )


# =============================================================================
# Offline Tests: Golden File Validation
# =============================================================================


class TestGoldenFiles:
    """Validate golden .rm files can be parsed."""

    @pytest.mark.parametrize("device", list(CalibrableDevice), ids=lambda d: d.value)
    def test_structure_golden_can_be_parsed(self, device: CalibrableDevice):
        """Structure golden file should be parseable."""
        from rmscene import read_blocks

        device_dir = get_device_dir(device)
        rm_path = device_dir / "calibration_structure.rm"

        if not rm_path.exists():
            pytest.skip(f"No calibration_structure.rm for {device.value}")

        with open(rm_path, "rb") as f:
            blocks = list(read_blocks(f))

        assert len(blocks) > 0, "Golden file has no blocks"

    @pytest.mark.parametrize("device", list(CalibrableDevice), ids=lambda d: d.value)
    def test_wrap_golden_can_be_parsed(self, device: CalibrableDevice):
        """Wrap golden file should be parseable."""
        from rmscene import read_blocks

        device_dir = get_device_dir(device)
        rm_path = device_dir / "calibration_wrap.rm"

        if not rm_path.exists():
            pytest.skip(f"No calibration_wrap.rm for {device.value}")

        with open(rm_path, "rb") as f:
            blocks = list(read_blocks(f))

        assert len(blocks) > 0, "Golden file has no blocks"


# =============================================================================
# Offline Tests: Cross-Page Accuracy (No calibration data needed)
# =============================================================================


class TestCrossPageAccuracy:
    """Tests for cross-page annotation positioning.

    These tests validate the layout engine's calculations without
    requiring captured calibration data.
    """

    def test_position_calculation_uses_correct_line_height(self, layout_engine):
        """Y position should increment by line_height per line."""
        text = "line1\nline2\nline3"
        origin = (-375.0, 94.0)
        width = 758.0

        # Get positions for start of each line
        pos0 = layout_engine.offset_to_position(0, text, origin, width)
        pos1 = layout_engine.offset_to_position(6, text, origin, width)  # "line2"
        pos2 = layout_engine.offset_to_position(12, text, origin, width)  # "line3"

        line_height = layout_engine.line_height

        # Y should increment by line_height
        assert abs((pos1[1] - pos0[1]) - line_height) < 1.0
        assert abs((pos2[1] - pos1[1]) - line_height) < 1.0

    def test_line_breaks_are_consistent(self, layout_engine):
        """Line breaks should be deterministic."""
        text = "The quick brown fox jumps over the lazy dog. " * 5
        width = 758.0

        breaks1 = layout_engine.calculate_line_breaks(text, width)
        breaks2 = layout_engine.calculate_line_breaks(text, width)

        assert breaks1 == breaks2, "Line breaks should be deterministic"
