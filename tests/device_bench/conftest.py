"""Pytest configuration for device tests.

Provides fixtures and markers for device-interactive tests.

Usage:
    # Run all device tests
    uv run pytest tests/device_bench -m device

    # Run without cleanup (for debugging)
    uv run pytest tests/device_bench --no-cleanup

    # Run specific test
    uv run pytest tests/device_bench -k annotation_roundtrip
"""

import pytest
from pathlib import Path


# Import lazily to avoid import issues when running from different directories
def _get_bench():
    from tests.device_bench.harness import Bench
    return Bench


def _get_workspace_manager():
    from tests.device_bench.harness import WorkspaceManager
    return WorkspaceManager


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


@pytest.fixture
def testdata_dir(fixtures_dir: Path) -> Path:
    """Get OCR handwriting testdata directory."""
    return fixtures_dir / "testdata" / "ocr_handwriting"


@pytest.fixture
def has_testdata(testdata_dir: Path) -> bool:
    """Check if OCR handwriting testdata exists."""
    manifest = testdata_dir / "manifest.json"
    return manifest.exists() and list(testdata_dir.glob("*.rm"))
