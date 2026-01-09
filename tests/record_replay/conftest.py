"""Pytest configuration for device tests.

Provides fixtures and markers for device-interactive tests with online/offline modes.

Usage:
    # Run offline tests (replaying pre-recorded testdata with rmfakecloud)
    uv run pytest tests/record_replay -m offline_only

    # Run online tests (recording with real reMarkable device)
    uv run pytest tests/record_replay --online -s

    # Replay specific test artifact in offline mode
    uv run pytest tests/record_replay --offline --test-artifact=highlights

    # List available offline tests
    uv run pytest tests/record_replay --list-tests

    # Run without cleanup (for debugging)
    uv run pytest tests/record_replay --no-cleanup
"""

# Make sure json is available at module level
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.record_replay.harness import DeviceInteractionProtocol


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
        help="Folder name on reMarkable device (offline mode only; online mode uses config.toml)",
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
        help="Run online tests with real device (implies --capture=no for interactive prompts)",
    )


def pytest_configure(config):
    """Register device test markers and setup options."""
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

    # If --online flag is set, require -s flag (no capture)
    if config.getoption("--online", default=False):
        if config.option.capture != "no":
            raise pytest.UsageError(
                "ERROR: --online requires -s flag to disable output capture for interactive prompts\n"
                "Usage: uv run pytest tests/record_replay --online -s"
            )
        # Ensure capture is disabled for interactive prompts
        config.option.capture = "no"
        config.pluginmanager.set_blocked("cacheprovider")


def pytest_sessionstart(session):
    """Called after the Session object has been created.

    Re-verify capture is disabled if --online flag is set.
    """
    if session.config.getoption("--online", default=False):
        # Double-check capture is disabled
        capmanager = session.config.pluginmanager.get_plugin("capturemanager")
        if capmanager:
            # Force disable any capturing
            session.config.option.capture = "no"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Handle TestdataExistsError by skipping the test.

    When multiple tests share the same fixture, the first test records the testdata.
    Subsequent tests in the same session should reuse that testdata rather than
    re-recording. This hook catches TestdataExistsError and converts it to a skip.
    """
    from tests.record_replay.harness import TestdataExistsError

    try:
        outcome = yield
        outcome.get_result()
    except TestdataExistsError as e:
        pytest.skip(f"Reusing testdata recorded earlier this session: {e.test_id}")


def pytest_collection_modifyitems(config, items):
    """Handle --list-tests option and mode-based test selection."""
    # Handle --list-tests
    if config.getoption("--list-tests"):
        TestdataStore = _get_testdata_store()  # noqa: N806
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

    # Determine device mode from --online flag
    online = config.getoption("--online")

    for item in items:
        # Only skip tests that are explicitly marked as mode-specific
        if online and "offline_only" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="Test requires offline mode"))
        elif not online and "online_only" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="Test requires --online flag"))


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
    Bench = _get_bench()  # noqa: N806
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

    Credential handling:
    - Online mode: Uses user's actual device credentials from XDG config
      (tests should use --device-folder to isolate to test vault)
    - Offline mode: Uses test credentials for rmfakecloud authentication
    """
    import json

    WorkspaceManager = _get_workspace_manager()  # noqa: N806
    no_cleanup = request.config.getoption("--no-cleanup")
    online = request.config.getoption("--online")

    # Determine device mode: online if --online flag set, else offline
    device_mode = "online" if online else "offline"

    # Determine cloud_url based on mode (SINGLE SOURCE OF TRUTH)
    # - Offline mode: use rmfakecloud fixture
    # - Online mode: use user's actual cloud URL from their config
    if device_mode == "offline":
        cloud_url = rmfakecloud
        device_folder = request.config.getoption("--device-folder")
    else:
        # Online mode: read cloud URL from user's actual config (REQUIRED)
        from rock_paper_sync.config import load_config

        user_config_path = Path.home() / ".config" / "rock-paper-sync" / "config.toml"
        if not user_config_path.exists():
            bench.error(
                f"User config not found at {user_config_path}\n"
                f"Online tests require: uv run rock-paper-sync init"
            )
            pytest.skip("User config required for online tests")

        user_config = load_config(user_config_path)
        cloud_url = user_config.cloud.base_url
        bench.ok(f"Using user's cloud URL: {cloud_url}")
        device_folder = None  # Online mode uses vaults from user's config

    # Create appropriate vault manager for the device mode
    if device_mode == "offline":
        OfflineVault = _get_offline_vault()  # noqa: N806
        vault = OfflineVault(workspace_dir, bench, testdata_store)
    else:
        OnlineVault = _get_online_vault()  # noqa: N806
        vault = OnlineVault(workspace_dir, bench, testdata_store)

    ws = WorkspaceManager(workspace_dir, repo_root, bench, vault, device_folder, cloud_url)
    ws.setup()

    # Setup test fixtures and credentials depending on device mode
    from tests.fixtures.rmfakecloud.helpers import get_credentials as get_rmfakecloud_credentials

    fixtures_dir = Path(__file__).parent / "fixtures"
    test_config_file = fixtures_dir / "config.toml"

    if device_mode == "offline":
        # Offline mode: Use TEMPORARY credentials directory to avoid overwriting user's real credentials
        # Create test credentials in workspace, not user's config directory
        test_config_home = workspace_dir / ".test_config"
        creds_dir = test_config_home / "rock-paper-sync"
        creds_path = creds_dir / "device-credentials.json"

        try:
            creds_data = get_rmfakecloud_credentials()
            creds_dir.mkdir(parents=True, exist_ok=True)
            creds_path.write_text(json.dumps(creds_data, indent=2))

            # Verify file was written
            import subprocess

            file_check = subprocess.run(
                ["ls", "-la", str(creds_path)], capture_output=True, text=True
            )
            bench.ok(f"Created test credentials at {creds_path}")
            bench.ok(
                f"File verification: {file_check.stdout.strip() if file_check.returncode == 0 else 'FAILED'}"
            )

            # Override XDG_CONFIG_HOME so rock-paper-sync uses test credentials
            # rock-paper-sync looks for $XDG_CONFIG_HOME/rock-paper-sync/device-credentials.json
            os.environ["XDG_CONFIG_HOME"] = str(test_config_home)
            bench.ok(f"Set XDG_CONFIG_HOME={os.environ['XDG_CONFIG_HOME']}")

            # Double-check with subprocess
            test_script = f"""
import os
from pathlib import Path
print(f'XDG_CONFIG_HOME={{os.getenv("XDG_CONFIG_HOME")}}')
p = Path('{creds_path}')
print(f'File exists: {{p.exists()}}')
if p.exists():
    print(f'File size: {{p.stat().st_size}}')
"""
            env_check = subprocess.run(
                ["python3", "-c", test_script], capture_output=True, text=True
            )
            if env_check.returncode == 0:
                bench.ok(f"Subprocess verification:\n{env_check.stdout}")
            else:
                bench.warn(f"Subprocess check failed: {env_check.stderr}")
        except FileNotFoundError as e:
            bench.warn(f"Test credentials not found: {e}")

        # Copy test config.toml to workspace using format() templating
        if test_config_file.exists():
            config_template = test_config_file.read_text()
            config_context = {
                "workspace": str(workspace_dir.resolve()),
                "cloud_url": rmfakecloud,
                "allow_paragraph_splitting": "false",
            }
            expanded_config = config_template.format(**config_context)
            workspace_config = workspace_dir / "config.toml"
            workspace_config.write_text(expanded_config)
            # Store template info in workspace for reconfiguration
            ws._config_template = config_template
            ws._config_context = config_context
            bench.ok(f"Using test config at {workspace_config}")

    elif device_mode == "online":
        # Online mode: Use user's real credentials and real cloud, with isolated test vault
        creds_dir = Path.home() / ".config" / "rock-paper-sync"
        creds_path = creds_dir / "device-credentials.json"

        if not creds_path.exists():
            bench.error(
                f"Device credentials not found at {creds_path}\n"
                f"Online tests require credentials from: uv run rock-paper-sync register"
            )
            pytest.skip("Device credentials required for online tests")

        bench.ok(f"Using real device credentials from {creds_path}")

        # Generate test config with real cloud URL but isolated test vault
        if test_config_file.exists():
            config_template = test_config_file.read_text()
            config_context = {
                "workspace": str(workspace_dir.resolve()),
                "cloud_url": cloud_url,
                "allow_paragraph_splitting": "false",
            }
            expanded_config = config_template.format(**config_context)
            workspace_config = workspace_dir / "config.toml"
            workspace_config.write_text(expanded_config)
            # Store template info in workspace for reconfiguration
            ws._config_template = config_template
            ws._config_context = config_context
            bench.ok(f"Using test vault config at {workspace_config}")
        else:
            bench.warn(
                f"Test config not found at {test_config_file}\n"
                f"Online tests require a test vault configuration in fixtures/config.toml"
            )

    yield ws

    # Cleanup unless --no-cleanup specified
    if not no_cleanup:
        ws.cleanup()

        if device_mode == "offline":
            # Clean up test credentials (offline mode only)
            if creds_path.exists():
                try:
                    creds_data = json.loads(creds_path.read_text())
                    test_data = get_rmfakecloud_credentials()
                    # Only delete if it's our test credentials (same device_token)
                    if creds_data.get("device_token") == test_data.get("device_token"):
                        creds_path.unlink()
                        bench.ok("Cleaned up test credentials")
                except Exception as e:
                    bench.warn(f"Failed to clean up test credentials: {e}")
        # Online mode: uses user's real credentials, no cleanup needed


@pytest.fixture(scope="function")
def testdata_store(fixtures_dir: Path, tmp_path: Path):
    """Create TestdataStore instance with test-specific diagnostic directory."""
    TestdataStore = _get_testdata_store()  # noqa: N806
    # Testdata is now at tests/record_replay/testdata/
    testdata_dir = fixtures_dir.parent / "testdata"
    diagnostic_dir = tmp_path / "diagnostics"
    print(f"\n[testdata_store] Diagnostics: {diagnostic_dir}")
    return TestdataStore(testdata_dir, diagnostic_dir=diagnostic_dir)


@pytest.fixture(scope="function")
def device(request, workspace, testdata_store, bench, rmfakecloud) -> "DeviceInteractionProtocol":
    """Create device instance based on --online flag.

    Returns a DeviceInteractionProtocol implementation:
    - In online mode (--online): OnlineDevice with real device interaction
    - In offline mode (no --online): OfflineEmulator with pre-recorded testdata

    The Protocol ensures type safety - both implementations must satisfy
    the same interface contract, catching signature mismatches at type-check time.

    Returns:
        DeviceInteractionProtocol: Device implementation (OnlineDevice or OfflineEmulator)

    Usage in tests:
        def test_annotation(device: DeviceInteractionProtocol, workspace):
            doc_uuid = device.upload_document(workspace.test_doc)
            state = device.wait_for_annotations(doc_uuid)
            assert state.has_annotations
    """
    online = request.config.getoption("--online")
    test_artifact = request.config.getoption("--test-artifact")

    if online:
        OnlineDevice = _get_online_device()  # noqa: N806
        dev = OnlineDevice(workspace, testdata_store, bench)
    else:
        OfflineEmulator = _get_offline_emulator()  # noqa: N806
        # cloud_url comes from workspace (set during workspace setup)
        dev = OfflineEmulator(workspace, testdata_store, bench)

        # Load specific test artifact if provided
        if test_artifact:
            dev.load_test(test_artifact)

    # Note: Tests should call dev.start_test(test_id) themselves if needed.
    # This allows tests to use custom test IDs or handle offline mode gracefully.
    yield dev

    # Teardown: cleanup device-specific state
    # Online mode: prompts user to confirm device cleanup
    # Offline mode: silent cleanup (no user interaction)
    dev.cleanup()


@pytest.fixture
def device_mode(request) -> str:
    """Get current device mode (online or offline).

    Returns 'online' if --online flag is set, otherwise 'offline'.
    """
    return "online" if request.config.getoption("--online") else "offline"


@pytest.fixture
def testdata_dir(fixtures_dir: Path) -> Path:
    """Get OCR handwriting testdata directory."""
    # Testdata is now at tests/record_replay/testdata/
    return fixtures_dir.parent / "testdata" / "ocr_handwriting"


@pytest.fixture
def has_testdata(testdata_dir: Path) -> bool:
    """Check if OCR handwriting testdata exists."""
    manifest = testdata_dir / "manifest.json"
    rm_files_dir = testdata_dir / "rm_files"
    # Check manifest exists and .rm files are either in root or rm_files/ subdirectory
    return manifest.exists() and (
        list(testdata_dir.glob("*.rm")) or list(rm_files_dir.glob("*.rm"))
    )


# =============================================================================
# Container fixtures for rmfakecloud (supports Docker and Podman)
# =============================================================================


@pytest.fixture(scope="function")
def offline_device(
    request, workspace, testdata_store, bench, rmfakecloud
) -> "DeviceInteractionProtocol":
    """Create OfflineEmulator connected to containerized rmfakecloud.

    This fixture automatically starts rmfakecloud (Docker or Podman) and
    configures the emulator to use it. Use this for offline tests that
    should run automatically in CI.

    Returns:
        DeviceInteractionProtocol: OfflineEmulator instance

    Note: You must call `offline_device.load_test(test_id)` before using
    methods that require testdata (like wait_for_annotations).

    Usage:
        @pytest.mark.offline
        def test_annotation_replay(
            offline_device: DeviceInteractionProtocol,
            workspace,
            testdata_store
        ):
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
    OfflineEmulator = _get_offline_emulator()  # noqa: N806

    # cloud_url comes from workspace (set during workspace setup)
    dev = OfflineEmulator(
        workspace=workspace,
        testdata_store=testdata_store,
        bench=bench,
    )

    # Load test artifact if specified via CLI
    test_artifact = request.config.getoption("--test-artifact")
    if test_artifact:
        dev.load_test(test_artifact)

    yield dev


@pytest.fixture(scope="function")
def golden_comparison(fixtures_dir: Path, testdata_store):
    """Create GoldenComparison instance for validating markdown outputs.

    Used to compare test outputs against golden files in replay mode.
    Supports both testdata-colocated and legacy fixtures/goldens/ locations.

    Usage:
        def test_markdown_output(golden_comparison):
            output_file = Path("output.md")
            output_file.write_text("# Test Output")

            result = golden_comparison("test_id").compare(output_file, phase_name="final")
            golden_comparison("test_id").print_result(result)
            assert result.matches or result.is_first_run
    """
    from tests.record_replay.harness.golden_comparison import GoldenComparison

    goldens_dir = fixtures_dir / "goldens"

    def create_comparison(test_id: str) -> GoldenComparison:
        """Create a GoldenComparison instance for the given test ID.

        Checks testdata-colocated goldens first, falls back to legacy location.
        """
        return GoldenComparison(test_id, goldens_dir, testdata_store)

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
                        except (OSError, UnicodeDecodeError):
                            pass

            return vault_files

    replay = GoldenReplay(workspace, testdata_store, golden_comparison)
    yield replay


# =============================================================================
# OCR Service fixtures (supports local minimal OCR via docker-compose)
# =============================================================================


@pytest.fixture(scope="function")
def ocr_service():
    """Provide a LocalOCRService connected to the minimal OCR container.

    This fixture automatically starts the ocr-minimal service via podman-compose
    and provides a connected OCRService client.

    The minimal OCR service is lightweight and returns deterministic dummy results.
    It's suitable for testing but not for production use.

    Usage:
        def test_ocr_integration(ocr_service):
            from rock_paper_sync.ocr.protocol import OCRRequest, BoundingBox, ParagraphContext
            import base64
            from PIL import Image
            import io

            # Create a test image
            img = Image.new('RGB', (100, 100), color='white')
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')

            request = OCRRequest(
                image=img_bytes.getvalue(),
                annotation_uuid="test-1",
                bounding_box=BoundingBox(x=10, y=10, width=80, height=80),
                context=ParagraphContext(
                    document_id="doc-1",
                    page_number=1,
                    paragraph_index=0,
                    paragraph_text="test text"
                )
            )

            result = ocr_service.recognize(request)
            assert result.text is not None
            assert result.confidence > 0
    """
    import subprocess
    import time

    from rock_paper_sync.ocr.local import LocalOCRService

    # Start OCR minimal service using podman-compose
    compose_dir = Path(__file__).parent

    # Start the service
    result = subprocess.run(
        [
            "podman-compose",
            "-f",
            str(compose_dir / "docker-compose.yml"),
            "up",
            "-d",
            "ocr-minimal",
        ],
        cwd=compose_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to start OCR service: {result.stderr}")

    # Wait for service to be healthy
    service = None
    max_retries = 30
    try:
        for i in range(max_retries):
            try:
                service = LocalOCRService(container_url="http://localhost:8000", timeout=5.0)
                if service.health_check():
                    yield service
                    return
            except Exception:
                pass

            time.sleep(0.5)

        # If we get here, service failed to start
        raise RuntimeError("OCR minimal service failed to start after 15 seconds")

    finally:
        # Always cleanup the service
        if service is not None:
            try:
                service.close()
            except Exception:
                pass

        # Stop the container
        try:
            subprocess.run(
                ["podman-compose", "-f", str(compose_dir / "docker-compose.yml"), "down"],
                cwd=compose_dir,
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass


# =============================================================================
# Visual Comparison fixtures for stroke validation
# =============================================================================


@pytest.fixture(scope="function")
def visual_validator(testdata_store, tmp_path):
    """Provide visual comparison utilities for validating stroke rendering.

    This fixture provides functions to compare .rm files visually using
    PNG rendering and perceptual hashing. It's useful for validating that
    strokes are rendered in the correct positions.

    Example:
        def test_stroke_positions(visual_validator, device, workspace):
            # ... test code that produces test_rm_files ...

            # Compare against golden
            result = visual_validator.compare(
                test_rm_files=test_state.rm_files,
                golden_rm_files=golden_state.rm_files,
            )
            visual_validator.assert_match(result, max_hash_distance=15)

            # Or use the shorthand
            visual_validator.assert_visual_match(
                test_state.rm_files,
                golden_state.rm_files,
            )
    """
    from tests.record_replay.harness.visual_comparison import (
        VisualComparisonResult,
        compare_rm_files_visually,
        print_visual_comparison,
        save_comparison_debug_images,
    )

    class VisualValidator:
        """Helper class for visual validation in tests."""

        def __init__(self, testdata_store, debug_dir: Path):
            self.testdata_store = testdata_store
            self.debug_dir = debug_dir

        def compare(
            self,
            test_rm_files: dict[str, bytes],
            golden_rm_files: dict[str, bytes],
            padding: int = 50,
            position_tolerance: float = 100.0,
        ) -> VisualComparisonResult:
            """Compare test .rm files against golden visually.

            Args:
                test_rm_files: page_uuid -> .rm bytes from test output
                golden_rm_files: page_uuid -> .rm bytes from golden reference
                padding: Pixels to add around each stroke region
                position_tolerance: Max center distance to match strokes

            Returns:
                VisualComparisonResult with match details
            """
            return compare_rm_files_visually(
                test_rm_files,
                golden_rm_files,
                padding=padding,
                position_tolerance=position_tolerance,
            )

        def assert_match(
            self,
            result: VisualComparisonResult,
            max_hash_distance: int = 15,
        ) -> None:
            """Assert that visual comparison passed.

            Args:
                result: Comparison result to validate
                max_hash_distance: Maximum allowed perceptual hash distance

            Raises:
                AssertionError: If comparison failed
            """
            if result.render_errors:
                raise AssertionError(
                    "Failed to render .rm files:\n"
                    + "\n".join(f"  - {e}" for e in result.render_errors)
                )

            if not result.all_matched:
                lines = [f"Missing {len(result.missing_in_test)} stroke(s) in test output:"]
                for bbox in result.missing_in_test:
                    lines.append(f"  - bbox at ({bbox.x:.0f}, {bbox.y:.0f})")
                raise AssertionError("\n".join(lines))

            failures = [m for m in result.matches if not m.within_threshold(max_hash_distance)]
            if failures:
                lines = [
                    f"Visual mismatch for {len(failures)} stroke(s) (threshold={max_hash_distance}):"
                ]
                for f in failures:
                    lines.append(f.format_diff())
                raise AssertionError("\n".join(lines))

        def assert_visual_match(
            self,
            test_rm_files: dict[str, bytes],
            golden_rm_files: dict[str, bytes],
            max_hash_distance: int = 15,
            padding: int = 50,
            position_tolerance: float = 100.0,
        ) -> VisualComparisonResult:
            """Compare and assert visual match in one call.

            Args:
                test_rm_files: page_uuid -> .rm bytes from test output
                golden_rm_files: page_uuid -> .rm bytes from golden reference
                max_hash_distance: Maximum allowed perceptual hash distance
                padding: Pixels around each stroke region
                position_tolerance: Max center distance to match strokes

            Returns:
                VisualComparisonResult on success

            Raises:
                AssertionError: If comparison failed
            """
            result = self.compare(
                test_rm_files,
                golden_rm_files,
                padding=padding,
                position_tolerance=position_tolerance,
            )
            self.assert_match(result, max_hash_distance)
            return result

        def print_comparison(
            self,
            test_rm_files: dict[str, bytes],
            golden_rm_files: dict[str, bytes],
        ) -> None:
            """Print detailed comparison results for debugging."""
            print_visual_comparison(test_rm_files, golden_rm_files)

        def save_debug_images(
            self,
            test_rm_files: dict[str, bytes],
            golden_rm_files: dict[str, bytes],
            name: str = "comparison",
            test_page_order: list[str] | None = None,
            golden_page_order: list[str] | None = None,
        ) -> list[Path]:
            """Save debug images for failed comparisons.

            Args:
                test_rm_files: page_uuid -> .rm bytes from test output
                golden_rm_files: page_uuid -> .rm bytes from golden reference
                name: Subdirectory name for debug images
                test_page_order: Optional page UUIDs in display order for test
                golden_page_order: Optional page UUIDs in display order for golden

            Returns:
                List of saved image paths
            """
            output_dir = self.debug_dir / name
            return save_comparison_debug_images(
                test_rm_files,
                golden_rm_files,
                output_dir,
                test_page_order=test_page_order,
                golden_page_order=golden_page_order,
            )

        def compare_with_testdata(
            self,
            test_id: str,
            test_rm_files: dict[str, bytes],
        ) -> VisualComparisonResult | None:
            """Compare test output against golden from testdata.

            Automatically loads golden .rm files from testdata and compares.

            Args:
                test_id: Test identifier
                test_rm_files: page_uuid -> .rm bytes from test output

            Returns:
                VisualComparisonResult or None if no golden data
            """
            # Try trip-based golden first
            if self.testdata_store.has_trips(test_id):
                golden = self.testdata_store.get_golden(test_id)
                if golden and golden.annotations:
                    return self.compare(test_rm_files, golden.annotations.rm_files)
            return None

        def load_uploaded_rm(
            self,
            test_id: str,
            trip_number: int,
            mode: str = "offline",
        ) -> tuple[dict[str, bytes], list[str]] | None:
            """Load uploaded_rm files from trip diagnostic directory.

            Args:
                test_id: Test identifier
                trip_number: Trip number (1-indexed)
                mode: "offline" or "online" diagnostic directory

            Returns:
                Tuple of (rm_files dict, page_order list), or None if not found
            """
            diag_path = self.testdata_store.get_trip_diagnostic_dir(test_id, trip_number)
            if not diag_path:
                return None

            # Try mode-specific path first, then legacy path
            uploaded_dir = diag_path / mode / "uploaded_rm"
            if not uploaded_dir.exists():
                uploaded_dir = diag_path / "uploaded_rm"
            if not uploaded_dir.exists():
                return None

            rm_dir = uploaded_dir / "rm_files"
            if not rm_dir.exists():
                return None

            rm_files = {}
            for rm_file in rm_dir.glob("*.rm"):
                rm_files[rm_file.stem] = rm_file.read_bytes()

            if not rm_files:
                return None

            # Load page order from metadata
            page_order: list[str] = []
            metadata_file = uploaded_dir / "metadata.json"
            if metadata_file.exists():
                import json

                metadata = json.loads(metadata_file.read_text())
                page_order = metadata.get("page_order", [])

            return rm_files, page_order

        def assert_uploaded_matches_golden(
            self,
            test_id: str,
            trip_number: int,
            mode: str = "offline",
            max_hash_distance: int = 15,
            save_debug: bool = True,
        ) -> VisualComparisonResult:
            """Assert that uploaded_rm for a trip matches golden visually.

            This is the primary assertion for visual comparison tests.

            Args:
                test_id: Test identifier
                trip_number: Trip number to compare (1-indexed)
                mode: "offline" or "online" diagnostic directory
                max_hash_distance: Maximum allowed perceptual hash distance
                save_debug: Save debug images on failure

            Returns:
                VisualComparisonResult on success

            Raises:
                AssertionError: If comparison failed or data missing
            """
            # Load uploaded_rm with page order
            result = self.load_uploaded_rm(test_id, trip_number, mode)
            if not result:
                raise AssertionError(
                    f"No uploaded_rm found for {test_id} trip {trip_number} ({mode} mode). "
                    f"Run the test to capture diagnostic files."
                )
            uploaded_rm, test_page_order = result

            # Load golden with page order
            golden = self.testdata_store.get_golden(test_id)
            if not golden or not golden.annotations:
                raise AssertionError(f"No golden annotations for {test_id}")
            golden_rm = golden.annotations.rm_files
            golden_page_order = golden.annotations.page_uuids

            # Compare and assert
            comparison = self.compare(uploaded_rm, golden_rm)

            # Save debug images before assertion (so they're available on failure)
            if save_debug:
                saved = self.save_debug_images(
                    uploaded_rm,
                    golden_rm,
                    name=test_id,
                    test_page_order=test_page_order,
                    golden_page_order=golden_page_order,
                )
                if saved:
                    print(f"\nDebug images saved to: {saved[0].parent}")

            self.assert_match(comparison, max_hash_distance)
            return comparison

    debug_dir = tmp_path / "visual_debug"
    print(f"\n[visual_validator] Debug output: {debug_dir}")
    return VisualValidator(testdata_store, debug_dir)
