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

import pytest
from pathlib import Path


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
        assert "3000" in offline_device.cloud_url

    def test_offline_device_can_load_testdata(self, offline_device, testdata_store):
        """Verify offline device can load testdata."""
        offline_device.load_test(LEGACY_OCR_TEST_ID)

        assert offline_device._current_artifacts is not None
        assert offline_device._current_artifacts.manifest.test_id == LEGACY_OCR_TEST_ID
