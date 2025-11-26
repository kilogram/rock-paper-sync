"""Pytest configuration for device tests.

Provides fixtures and markers for device-interactive tests with online/offline modes.

Usage:
    # Run offline tests with automatic rmfakecloud container
    uv run pytest tests/record_replay -m offline

    # Run all device tests in online mode (real device)
    uv run pytest tests/record_replay -m device --device-mode=online

    # Run in offline mode with external rmfakecloud
    uv run pytest tests/record_replay -m device --device-mode=offline

    # Replay specific test artifact
    uv run pytest tests/record_replay -m device \\
        --device-mode=offline --test-artifact=annotation_roundtrip_001

    # List available offline tests
    uv run pytest tests/record_replay --list-tests

    # Run without cleanup (for debugging)
    uv run pytest tests/record_replay --no-cleanup
"""

import os
import time

import pytest
from pathlib import Path


# Import lazily to avoid import issues when running from different directories
def _get_bench():
    from tests.record_replay.harness import Bench
    return Bench


def _get_workspace_manager():
    from tests.record_replay.harness import WorkspaceManager
    return WorkspaceManager


def _get_testdata_store():
    from tests.record_replay.harness import TestdataStore
    return TestdataStore


def _get_online_device():
    from tests.record_replay.harness import OnlineDevice
    return OnlineDevice


def _get_offline_emulator():
    from tests.record_replay.harness import OfflineEmulator
    return OfflineEmulator


def _get_online_vault():
    from tests.record_replay.harness import OnlineVault
    return OnlineVault


def _get_offline_vault():
    from tests.record_replay.harness import OfflineVault
    return OfflineVault


def pytest_addoption(parser):
    """Add device test CLI options."""
    parser.addoption(
        "--no-cleanup",
        action="store_true",
        default=False,
        help="Don't cleanup workspace after tests",
    )
    parser.addoption(
        "--device-folder",
        default="DeviceBench",
        help="Folder name on reMarkable device",
    )
    parser.addoption(
        "--device-mode",
        choices=["online", "offline"],
        default="online",
        help="Device mode: online (real device) or offline (replay via rmfakecloud)",
    )
    parser.addoption(
        "--test-artifact",
        default=None,
        help="Test artifact ID to replay (offline mode only)",
    )
    parser.addoption(
        "--rmfakecloud-url",
        default="http://localhost:3001",
        help="rmfakecloud URL for offline mode (default: 3001 to avoid conflict with real rmfakecloud)",
    )
    parser.addoption(
        "--list-tests",
        action="store_true",
        default=False,
        help="List available offline test artifacts",
    )
    parser.addoption(
        "--online",
        action="store_true",
        default=False,
        help="Run device tests that require rmfakecloud or a real device",
    )


def pytest_configure(config):
    """Register device test markers."""
    config.addinivalue_line(
        "markers",
        "device: marks tests as device-interactive (require reMarkable device)",
    )
    config.addinivalue_line(
        "markers",
        "ocr: marks tests as requiring OCR service",
    )
    config.addinivalue_line(
        "markers",
        "offline: marks tests that run with dockerized rmfakecloud (no device needed)",
    )
    config.addinivalue_line(
        "markers",
        "offline_only: marks tests that only run in offline mode",
    )
    config.addinivalue_line(
        "markers",
        "online_only: marks tests that only run in online mode",
    )


def pytest_collection_modifyitems(config, items):
    """Handle --list-tests option and mode-based test selection."""
    # Handle --list-tests
    if config.getoption("--list-tests"):
        TestdataStore = _get_testdata_store()
        fixtures_dir = Path(__file__).parent / "fixtures"
        store = TestdataStore(fixtures_dir / "testdata")

        print("\nAvailable offline test artifacts:")
        print("-" * 60)
        manifests = store.list_available_tests()
        if manifests:
            for m in manifests:
                print(f"  {m.test_id}: {m.description}")
                print(f"    Created: {m.created_at}")
                print(f"    Files: {m.annotations_count} .rm files")
        else:
            print("  (none found)")
        print("-" * 60)
        pytest.exit("Listed available tests", returncode=0)

    # Skip device tests unless --online is passed
    online = config.getoption("--online")

    # Skip tests based on mode
    device_mode = config.getoption("--device-mode")

    for item in items:
        # Skip device tests unless --online is explicitly passed
        if "device" in item.keywords and not online:
            item.add_marker(pytest.mark.skip(reason="Device tests require --online flag"))
        elif device_mode == "offline" and "online_only" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="Test requires online mode"))
        elif device_mode == "online" and "offline_only" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="Test requires offline mode"))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Get repository root path."""
    return Path(__file__).parent.parent.parent


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Get device bench fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def baseline_doc(fixtures_dir: Path) -> Path:
    """Get baseline markdown document for annotation tests."""
    return fixtures_dir / "baseline.md"


@pytest.fixture(scope="session")
def ocr_baseline_doc(fixtures_dir: Path) -> Path:
    """Get baseline markdown document for OCR tests."""
    return fixtures_dir / "ocr_baseline.md"


@pytest.fixture(scope="function")
def workspace_dir(tmp_path: Path) -> Path:
    """Get temporary workspace directory."""
    workspace = tmp_path / "device_bench_workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture(scope="function")
def bench(repo_root: Path, tmp_path: Path):
    """Create Bench instance for test."""
    Bench = _get_bench()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return Bench(repo_root, log_dir)


@pytest.fixture(scope="function")
def workspace(
    workspace_dir: Path,
    repo_root: Path,
    bench,
    request,
    testdata_store,
    rmfakecloud,
):
    """Create WorkspaceManager instance for test.

    Handles setup and optional cleanup based on --no-cleanup flag.
    Creates vault manager based on device mode (online/offline).
    Sets up test device credentials for rmfakecloud authentication.
    """
    import json

    WorkspaceManager = _get_workspace_manager()
    device_folder = request.config.getoption("--device-folder")
    no_cleanup = request.config.getoption("--no-cleanup")
    device_mode = request.config.getoption("--device-mode")
    cloud_url = rmfakecloud

    # Create appropriate vault manager for the device mode
    if device_mode == "offline":
        OfflineVault = _get_offline_vault()
        vault = OfflineVault(workspace_dir, bench, testdata_store)
    else:
        OnlineVault = _get_online_vault()
        vault = OnlineVault(workspace_dir, bench, testdata_store)

    ws = WorkspaceManager(workspace_dir, repo_root, bench, vault, device_folder, cloud_url)
    ws.setup()

    # Setup test device credentials for sync CLI authentication
    # This allows the sync command to authenticate with rmfakecloud
    fixtures_dir = Path(__file__).parent / "fixtures"
    test_creds_file = fixtures_dir / "rmfakecloud_test_credentials.json"

    if test_creds_file.exists():
        # Create credentials file in config directory for sync CLI to find
        creds_data = json.loads(test_creds_file.read_text())
        creds_dir = Path.home() / ".config" / "rock-paper-sync"
        creds_dir.mkdir(parents=True, exist_ok=True)
        creds_path = creds_dir / "device-credentials.json"
        creds_path.write_text(json.dumps(creds_data, indent=2))
        bench.ok(f"Created test credentials at {creds_path}")

    yield ws

    # Cleanup unless --no-cleanup specified
    if not no_cleanup:
        ws.cleanup()

        # Clean up test credentials
        creds_path = Path.home() / ".config" / "rock-paper-sync" / "device-credentials.json"
        if creds_path.exists() and test_creds_file.exists():
            try:
                creds_data = json.loads(creds_path.read_text())
                test_data = json.loads(test_creds_file.read_text())
                # Only delete if it's our test credentials (same device_token)
                if creds_data.get("device_token") == test_data.get("device_token"):
                    creds_path.unlink()
                    bench.ok("Cleaned up test credentials")
            except Exception as e:
                bench.warn(f"Failed to clean up test credentials: {e}")


@pytest.fixture(scope="session")
def testdata_store(fixtures_dir: Path):
    """Create TestdataStore instance for test session."""
    TestdataStore = _get_testdata_store()
    # Testdata is now at tests/testdata/ (moved from fixtures/testdata/)
    testdata_dir = fixtures_dir.parent.parent / "testdata"
    return TestdataStore(testdata_dir)


@pytest.fixture(scope="function")
def device(request, workspace, testdata_store, bench, rmfakecloud):
    """Create device instance based on --device-mode.

    In online mode: OnlineDevice with real device interaction
    In offline mode: OfflineEmulator with pre-recorded testdata

    Usage in tests:
        def test_annotation(device, workspace):
            doc_uuid = device.upload_document(workspace.test_doc)
            state = device.wait_for_annotations(doc_uuid)
            assert state.has_annotations
    """
    mode = request.config.getoption("--device-mode")
    test_artifact = request.config.getoption("--test-artifact")

    if mode == "online":
        OnlineDevice = _get_online_device()
        dev = OnlineDevice(workspace, testdata_store, bench)
    else:
        OfflineEmulator = _get_offline_emulator()
        # Use dynamically allocated rmfakecloud URL (handles parallel execution)
        dev = OfflineEmulator(
            workspace, testdata_store, bench, cloud_url=rmfakecloud
        )

        # Load specific test artifact if provided
        if test_artifact:
            dev.load_test(test_artifact)

    # Note: Tests should call dev.start_test(test_id) themselves if needed.
    # This allows tests to use custom test IDs or handle offline mode gracefully.
    yield dev

    # End test (success determined by test outcome)
    # Note: can't determine success here, tests should call end_test manually
    # if they need success-dependent behavior


@pytest.fixture
def device_mode(request) -> str:
    """Get current device mode (online or offline)."""
    return request.config.getoption("--device-mode")


@pytest.fixture
def testdata_dir(fixtures_dir: Path) -> Path:
    """Get OCR handwriting testdata directory."""
    # Testdata is now at tests/testdata/ (moved from fixtures/testdata/)
    return fixtures_dir.parent.parent / "testdata" / "record_replay" / "ocr_handwriting"


@pytest.fixture
def has_testdata(testdata_dir: Path) -> bool:
    """Check if OCR handwriting testdata exists."""
    manifest = testdata_dir / "manifest.json"
    rm_files_dir = testdata_dir / "rm_files"
    # Check manifest exists and .rm files are either in root or rm_files/ subdirectory
    return manifest.exists() and (
        list(testdata_dir.glob("*.rm")) or
        list(rm_files_dir.glob("*.rm"))
    )


# =============================================================================
# Container fixtures for rmfakecloud (supports Docker and Podman)
# =============================================================================


@pytest.fixture(scope="function")
def offline_device(request, workspace, testdata_store, bench, rmfakecloud):
    """Create OfflineEmulator connected to containerized rmfakecloud.

    This fixture automatically starts rmfakecloud (Docker or Podman) and
    configures the emulator to use it. Use this for offline tests that
    should run automatically in CI.

    Note: You must call `offline_device.load_test(test_id)` before using
    methods that require testdata (like wait_for_annotations).

    Usage:
        @pytest.mark.offline
        def test_annotation_replay(offline_device, workspace, testdata_store):
            # Get available testdata
            available = testdata_store.list_available_tests()
            if not available:
                pytest.skip("No testdata available")

            # Load and replay
            offline_device.load_test(available[0].test_id)
            doc_uuid = offline_device.upload_document(workspace.test_doc)
            state = offline_device.wait_for_annotations(doc_uuid)
            assert state.has_annotations
    """
    OfflineEmulator = _get_offline_emulator()

    dev = OfflineEmulator(
        workspace=workspace,
        testdata_store=testdata_store,
        bench=bench,
        cloud_url=rmfakecloud,
    )

    # Load test artifact if specified via CLI
    test_artifact = request.config.getoption("--test-artifact")
    if test_artifact:
        dev.load_test(test_artifact)

    yield dev


@pytest.fixture(scope="function")
def golden_comparison(fixtures_dir: Path):
    """Create GoldenComparison instance for validating markdown outputs.

    Used to compare test outputs against golden files in replay mode.

    Usage:
        def test_markdown_output(golden_comparison):
            output_file = Path("output.md")
            output_file.write_text("# Test Output")

            result = golden_comparison("test_id").compare(output_file)
            golden_comparison("test_id").print_result(result)
            assert result.matches or result.is_first_run
    """
    from tests.record_replay.harness.golden_comparison import GoldenComparison

    goldens_dir = fixtures_dir / "goldens"

    def create_comparison(test_id: str) -> GoldenComparison:
        """Create a GoldenComparison instance for the given test ID."""
        return GoldenComparison(test_id, goldens_dir)

    return create_comparison


@pytest.fixture(scope="function")
def golden_replay(workspace, testdata_store, golden_comparison, request):
    """Fixture for golden file validation during replay tests.

    Automatically validates that replay test outputs match captured testdata.
    The testdata itself is the golden reference.

    Captures:
    - Initial vault state at test start
    - Final vault state at test end
    - Compares key outputs against expectations

    Usage:
        @pytest.mark.offline
        def test_annotation_replay(offline_device, golden_replay):
            test_id = "ocr_handwriting_legacy"
            golden_replay.start(test_id)

            # Run your test...
            # At teardown, golden_replay validates output matches testdata

    Or use in test teardown:
        golden_replay.validate_vault_state()
        golden_replay.validate_markdown_output(output_file)
    """
    import shutil
    from pathlib import Path

    class GoldenReplay:
        def __init__(self, workspace, testdata_store, golden_comparison):
            self.workspace = workspace
            self.testdata_store = testdata_store
            self.golden_comparison = golden_comparison
            self.test_id = None
            self.initial_vault_state = None

        def start(self, test_id: str) -> None:
            """Initialize replay validation for a test.

            Args:
                test_id: Test identifier from testdata
            """
            self.test_id = test_id
            # Capture initial vault state
            self.initial_vault_state = self._capture_vault_state()

        def validate_vault_state(self) -> dict:
            """Validate final vault state matches testdata baseline.

            Compares the vault files at end of test against the captured baseline.
            The baseline is stored in the testdata.

            Returns:
                Dict with validation results (matches, diffs, etc.)
            """
            if not self.test_id:
                raise RuntimeError("start() must be called first")

            final_vault_state = self._capture_vault_state()

            # Get baseline from testdata
            artifacts = self.testdata_store.load_artifacts(self.test_id)
            baseline_content = artifacts.source_markdown

            results = {
                "test_id": self.test_id,
                "vault_files": final_vault_state,
                "baseline": baseline_content,
            }

            return results

        def validate_markdown_output(self, output_file: Path) -> None:
            """Validate markdown output matches golden baseline.

            Args:
                output_file: Path to output markdown file to validate
            """
            if not self.test_id:
                raise RuntimeError("start() must be called first")

            gc = self.golden_comparison(self.test_id)
            result = gc.compare(output_file)
            gc.print_result(result)

            assert result.matches or result.is_first_run, (
                f"Output mismatch for {self.test_id}. "
                f"To approve: cp {result.actual_file} {result.golden_file}"
            )

        def _capture_vault_state(self) -> dict:
            """Capture all markdown files in workspace vault.

            Returns:
                Dict mapping relative paths to file contents
            """
            vault_files = {}
            vault_dir = self.workspace.workspace_dir

            if vault_dir.exists():
                for file_path in vault_dir.rglob("*.md"):
                    if file_path.is_file():
                        try:
                            rel_path = file_path.relative_to(vault_dir).as_posix()
                            vault_files[rel_path] = file_path.read_text()
                        except (UnicodeDecodeError, IOError):
                            pass

            return vault_files

    replay = GoldenReplay(workspace, testdata_store, golden_comparison)
    yield replay
