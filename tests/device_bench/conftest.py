"""Pytest configuration for device tests.

Provides fixtures and markers for device-interactive tests with online/offline modes.

Usage:
    # Run offline tests with automatic rmfakecloud container
    uv run pytest tests/device_bench -m offline

    # Run all device tests in online mode (real device)
    uv run pytest tests/device_bench -m device --device-mode=online

    # Run in offline mode with external rmfakecloud
    uv run pytest tests/device_bench -m device --device-mode=offline

    # Replay specific test artifact
    uv run pytest tests/device_bench -m device \\
        --device-mode=offline --test-artifact=annotation_roundtrip_001

    # List available offline tests
    uv run pytest tests/device_bench --list-tests

    # Run without cleanup (for debugging)
    uv run pytest tests/device_bench --no-cleanup
"""

import os
import time

import pytest
from pathlib import Path


# Import lazily to avoid import issues when running from different directories
def _get_bench():
    from tests.device_bench.harness import Bench
    return Bench


def _get_workspace_manager():
    from tests.device_bench.harness import WorkspaceManager
    return WorkspaceManager


def _get_testdata_store():
    from tests.device_bench.harness import TestdataStore
    return TestdataStore


def _get_online_device():
    from tests.device_bench.harness import OnlineDevice
    return OnlineDevice


def _get_offline_emulator():
    from tests.device_bench.harness import OfflineEmulator
    return OfflineEmulator


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

    # Skip tests based on mode
    device_mode = config.getoption("--device-mode")

    for item in items:
        if device_mode == "offline" and "online_only" in item.keywords:
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
):
    """Create WorkspaceManager instance for test.

    Handles setup and optional cleanup based on --no-cleanup flag.
    """
    WorkspaceManager = _get_workspace_manager()
    device_folder = request.config.getoption("--device-folder")
    no_cleanup = request.config.getoption("--no-cleanup")

    ws = WorkspaceManager(workspace_dir, repo_root, bench, device_folder)
    ws.setup()

    yield ws

    # Cleanup unless --no-cleanup specified
    if not no_cleanup:
        ws.cleanup()


@pytest.fixture(scope="session")
def testdata_store(fixtures_dir: Path):
    """Create TestdataStore instance for test session."""
    TestdataStore = _get_testdata_store()
    return TestdataStore(fixtures_dir / "testdata")


@pytest.fixture(scope="function")
def device(request, workspace, testdata_store, bench):
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
    rmfakecloud_url = request.config.getoption("--rmfakecloud-url")

    if mode == "online":
        OnlineDevice = _get_online_device()
        dev = OnlineDevice(workspace, testdata_store, bench)
    else:
        OfflineEmulator = _get_offline_emulator()
        dev = OfflineEmulator(
            workspace, testdata_store, bench, cloud_url=rmfakecloud_url
        )

        # Load specific test artifact if provided
        if test_artifact:
            dev.load_test(test_artifact)

    # Start/end test lifecycle
    test_id = request.node.name
    dev.start_test(test_id)

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
    return fixtures_dir / "testdata" / "ocr_handwriting"


@pytest.fixture
def has_testdata(testdata_dir: Path) -> bool:
    """Check if OCR handwriting testdata exists."""
    manifest = testdata_dir / "manifest.json"
    return manifest.exists() and list(testdata_dir.glob("*.rm"))


# =============================================================================
# Container fixtures for rmfakecloud (supports Docker and Podman)
# =============================================================================


def _get_container_runtime() -> tuple[str, str]:
    """Detect container runtime and compose tool.

    Returns:
        Tuple of (runtime, compose_command) e.g., ("podman", "podman-compose")
    """
    import shutil

    # Check for podman first
    if shutil.which("podman"):
        if shutil.which("podman-compose"):
            return ("podman", "podman-compose")
        # podman can also run docker-compose files directly
        return ("podman", "podman compose")

    # Fall back to docker
    if shutil.which("docker"):
        if shutil.which("docker-compose"):
            return ("docker", "docker-compose")
        return ("docker", "docker compose")

    return ("", "")


def is_rmfakecloud_ready(url: str) -> bool:
    """Check if rmfakecloud is ready to accept connections."""
    import requests

    try:
        resp = requests.get(f"{url}/health", timeout=2)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _wait_for_ready(url: str, timeout: float = 30.0, interval: float = 0.5) -> bool:
    """Wait for service to become ready."""
    import time

    start = time.time()
    while time.time() - start < timeout:
        if is_rmfakecloud_ready(url):
            return True
        time.sleep(interval)
    return False


@pytest.fixture(scope="session")
def rmfakecloud_service(request):
    """Start rmfakecloud container and wait for it to be ready.

    Session-scoped, so the container is started once and shared.
    Works with both Docker and Podman.

    Returns:
        str: The URL of the rmfakecloud service
    """
    import subprocess

    runtime, _ = _get_container_runtime()
    if not runtime:
        pytest.skip("No container runtime found (docker/podman)")

    container_name = "device_bench_rmfakecloud"
    port = 3001  # Use different port than real rmfakecloud (3000)
    url = f"http://localhost:{port}"

    # Don't reuse existing - always want our test container
    # Check if our test container is already running
    result = subprocess.run(
        [runtime, "ps", "-q", "-f", f"name={container_name}"],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        # Our test container is running, check if ready
        if is_rmfakecloud_ready(url):
            yield url
            return

    # Remove any existing stopped container
    subprocess.run(
        [runtime, "rm", "-f", container_name],
        capture_output=True,
    )

    # Start container (use full registry path for podman compatibility)
    image = "docker.io/ddvk/rmfakecloud:latest"
    cmd = [
        runtime, "run", "-d",
        "--name", container_name,
        "-p", f"{port}:3000",
        "-e", f"STORAGE_URL=http://localhost:{port}",
        "-e", "JWT_SECRET_KEY=test-secret-key",
        image,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"Failed to start rmfakecloud: {result.stderr}")

    # Wait for ready
    if not _wait_for_ready(url, timeout=30.0):
        subprocess.run([runtime, "stop", container_name], capture_output=True)
        subprocess.run([runtime, "rm", container_name], capture_output=True)
        pytest.fail(f"rmfakecloud failed to become ready at {url}")

    yield url

    # Cleanup
    subprocess.run([runtime, "stop", container_name], capture_output=True)
    subprocess.run([runtime, "rm", container_name], capture_output=True)


@pytest.fixture(scope="function")
def offline_device(request, workspace, testdata_store, bench, rmfakecloud_service):
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
        cloud_url=rmfakecloud_service,
    )

    # Load test artifact if specified via CLI
    test_artifact = request.config.getoption("--test-artifact")
    if test_artifact:
        dev.load_test(test_artifact)

    yield dev
