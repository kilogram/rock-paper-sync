#!/usr/bin/env python3
"""Device test runner for interactive device testing.

This script provides the command-line interface for running device tests
in online (real device) or offline (replay via rmfakecloud) mode.

Usage:
    # Run all tests in online mode
    uv run python -m tests.device_bench.run_device_tests run

    # Run in offline mode
    uv run python -m tests.device_bench.run_device_tests run --mode=offline

    # Run specific test
    uv run python -m tests.device_bench.run_device_tests run --test annotation-roundtrip

    # List available offline tests
    uv run python -m tests.device_bench.run_device_tests list-tests

    # Extract testdata for automated testing
    uv run python -m tests.device_bench.run_device_tests extract-testdata

    # Export curated test set
    uv run python -m tests.device_bench.run_device_tests export-curated \\
        --set-name=basic_annotations --test-ids=test1,test2
"""

import shutil
import signal
import sys
from pathlib import Path

import click

from .harness import (
    Bench,
    OfflineEmulator,
    OnlineDevice,
    TestdataStore,
    WorkspaceManager,
)
from .harness.prompts import display_results, user_confirm
from .scenarios import ALL_TESTS, TESTS_BY_NAME

# Paths
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
FIXTURES_DIR = SCRIPT_DIR / "fixtures"
WORKSPACE_DIR = SCRIPT_DIR / "workspace"

# Fixture files
BASELINE_DOC = FIXTURES_DIR / "baseline.md"
OCR_BASELINE_DOC = FIXTURES_DIR / "ocr_baseline.md"

# Global cleanup state
_workspace: WorkspaceManager | None = None


def _signal_handler(signum, frame):
    """Handle interrupt signals."""
    global _workspace
    if _workspace:
        click.echo("\nInterrupted - cleaning up...")
        _workspace.cleanup()
    sys.exit(1)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def setup_workspace(device_folder: str = "DeviceBench") -> tuple[Bench, WorkspaceManager]:
    """Create and setup workspace."""
    global _workspace

    bench = Bench(REPO_ROOT, WORKSPACE_DIR / "logs")
    workspace = WorkspaceManager(WORKSPACE_DIR, REPO_ROOT, bench, device_folder)
    workspace.setup()
    _workspace = workspace

    return bench, workspace


def get_testdata_store() -> TestdataStore:
    """Get TestdataStore instance."""
    return TestdataStore(FIXTURES_DIR / "testdata")


@click.group()
@click.option(
    "--device-folder",
    default="DeviceBench",
    help="Folder name on reMarkable device",
)
@click.pass_context
def cli(ctx: click.Context, device_folder: str) -> None:
    """Device Test Runner for reMarkable device testing.

    Supports two modes:

    \b
    ONLINE MODE (default): Real device connected
      - User prompted for manual actions (annotating, syncing)
      - Testdata automatically captured for later replay

    \b
    OFFLINE MODE: No device needed
      - Pre-recorded testdata replayed via rmfakecloud
      - Enables CI testing without physical device
    """
    ctx.ensure_object(dict)
    ctx.obj["device_folder"] = device_folder


@cli.command()
@click.option(
    "--mode",
    type=click.Choice(["online", "offline"]),
    default="online",
    help="Device mode: online (real device) or offline (replay)",
)
@click.option(
    "--test",
    "test_name",
    type=click.Choice(list(TESTS_BY_NAME.keys())),
    help="Run specific test",
)
@click.option(
    "--test-artifact",
    help="Test artifact ID to replay (offline mode only)",
)
@click.option(
    "--rmfakecloud-url",
    default="http://localhost:3000",
    help="rmfakecloud URL for offline mode",
)
@click.option(
    "--no-cleanup",
    is_flag=True,
    help="Don't cleanup workspace after test",
)
@click.pass_context
def run(
    ctx: click.Context,
    mode: str,
    test_name: str | None,
    test_artifact: str | None,
    rmfakecloud_url: str,
    no_cleanup: bool,
) -> None:
    """Run device tests.

    In ONLINE mode, you'll be prompted to perform actions on your device.
    In OFFLINE mode, pre-recorded testdata is replayed via rmfakecloud.

    Examples:

    \b
        # Run all tests with real device
        run_device_tests run

    \b
        # Run specific test in offline mode
        run_device_tests run --mode=offline --test=annotation-roundtrip \\
            --test-artifact=annotation_roundtrip_001
    """
    device_folder = ctx.obj["device_folder"]
    bench, workspace = setup_workspace(device_folder)
    testdata_store = get_testdata_store()

    # Create device based on mode
    if mode == "online":
        device = OnlineDevice(workspace, testdata_store, bench)
        click.echo("Running in ONLINE mode (real device)")
    else:
        device = OfflineEmulator(workspace, testdata_store, bench, cloud_url=rmfakecloud_url)
        click.echo(f"Running in OFFLINE mode (rmfakecloud: {rmfakecloud_url})")

        if test_artifact:
            device.load_test(test_artifact)

    cleanup = not no_cleanup

    if test_name:
        # Run single test
        success = run_single_test(test_name, bench, workspace, device, cleanup, test_artifact)
        sys.exit(0 if success else 1)
    else:
        # Run all tests
        results = run_all_tests(bench, workspace, device, cleanup, test_artifact)
        passed = sum(1 for _, s in results if s)
        sys.exit(0 if passed == len(results) else 1)


def run_single_test(
    test_name: str,
    bench: Bench,
    workspace: WorkspaceManager,
    device: OnlineDevice | OfflineEmulator,
    cleanup: bool,
    test_artifact: str | None,
) -> bool:
    """Run a single test.

    Returns:
        True if test passed
    """
    # Get test class
    test_class = TESTS_BY_NAME.get(test_name)
    if not test_class:
        bench.error(f"Unknown test: {test_name}")
        return False

    # Setup document based on test type
    workspace.reset()
    if test_class.requires_ocr:
        workspace.setup_document(OCR_BASELINE_DOC)
    else:
        workspace.setup_document(BASELINE_DOC)

    # Start device test session
    test_id = test_artifact or test_name
    device.start_test(test_id)

    # Create and run test
    test = test_class(workspace, bench)
    result = test.run()

    # End device session
    device.end_test(test_id, result.success)

    # Save result
    bench.save_result(result)

    # Cleanup
    if cleanup:
        if result.success or test.cleanup_on_failure:
            workspace.cleanup()
        else:
            bench.warn(f"Workspace preserved at: {workspace.workspace_dir}")
            bench.info("Run with 'reset' command to cleanup manually")

    return result.success


def run_all_tests(
    bench: Bench,
    workspace: WorkspaceManager,
    device: OnlineDevice | OfflineEmulator,
    cleanup: bool,
    test_artifact: str | None,
) -> list[tuple[str, bool]]:
    """Run all tests.

    Returns:
        List of (test_name, success) tuples
    """
    bench.header("DEVICE TEST BENCH")

    # Display test info
    click.echo("""
Tests (each is self-contained):
  Annotation Tests:
  1. annotation-roundtrip - Full sync → annotate → verify markers
  2. no-hash-loop         - Verify markers don't cause re-upload
  3. content-edit         - Edit marked content → verify re-sync

  OCR Tests:
  4. ocr-recognition      - Write text → OCR → verify recognition
  5. ocr-correction       - OCR → correct text → verify correction stored
  6. ocr-stability        - Verify OCR markers don't cause re-upload

Requirements:
  - reMarkable device connected (online mode)
  - OR rmfakecloud running + testdata (offline mode)
  - Cloud at http://localhost:3000
  - OCR enabled in config (for OCR tests)
""")

    if not user_confirm("Continue?"):
        return []

    results = []
    for test_class in ALL_TESTS:
        # Setup for this test
        workspace.reset()
        if test_class.requires_ocr:
            workspace.setup_document(OCR_BASELINE_DOC)
        else:
            workspace.setup_document(BASELINE_DOC)

        # Start device session
        test_id = test_artifact or test_class.name
        device.start_test(test_id)

        # Run test
        test = test_class(workspace, bench)
        result = test.run()

        # End device session
        device.end_test(test_id, result.success)

        bench.save_result(result)
        results.append((test.name, result.success))

        # On failure, ask to continue
        if not result.success:
            if not user_confirm("Continue to next test?", default=False):
                break

    # Display summary
    display_results(results)

    # Final cleanup
    if cleanup:
        workspace.cleanup()

    return results


@cli.command("list-tests")
def list_tests() -> None:
    """List available offline test artifacts.

    Shows all test artifacts that can be used with --mode=offline.
    """
    store = get_testdata_store()
    manifests = store.list_available_tests()

    click.echo("\nAvailable offline test artifacts:")
    click.echo("-" * 60)

    if manifests:
        for m in manifests:
            click.echo(f"  {m.test_id}")
            click.echo(f"    Description: {m.description}")
            click.echo(f"    Created: {m.created_at}")
            click.echo(f"    Files: {m.annotations_count} .rm files")
            click.echo()
    else:
        click.echo("  (none found)")
        click.echo()
        click.echo("  Run tests in online mode to capture testdata:")
        click.echo("    run_device_tests run --mode=online")

    click.echo("-" * 60)


def _is_test_in_progress(workspace: WorkspaceManager) -> bool:
    """Check if a test is already in progress (state DB exists)."""
    state_db = workspace.state_dir / "state.db"
    return state_db.exists()


@cli.command("collect")
@click.argument("source_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--test-id",
    required=True,
    help="Unique identifier for this test (used for replay)",
)
@click.option(
    "--description",
    default="",
    help="Description of what this testdata captures",
)
@click.pass_context
def collect(
    ctx: click.Context,
    source_file: Path,
    test_id: str,
    description: str,
) -> None:
    """Collect testdata from any markdown file.

    Push SOURCE_FILE to device, wait for annotations, capture for replay.

    \b
    Auto-resumes if a test is in progress:
    - If workspace has state, skips upload and goes straight to sync-down
    - To start fresh, run 'reset' first or delete the workspace directory

    \b
    Workflow (fresh start):
    1. Syncs SOURCE_FILE to device
    2. User adds annotations on device
    3. Syncs back to download annotations
    4. Saves source + .rm files as testdata with TEST_ID

    \b
    Workflow (resume):
    1. Syncs back to download annotations
    2. Saves source + .rm files as testdata with TEST_ID

    \b
    Examples:
        # Collect from a custom scenario file
        run_device_tests collect my_scenario.md --test-id=custom_001

        # Resume after timeout (auto-detected)
        run_device_tests collect my_scenario.md --test-id=custom_001

        # Force fresh start
        run_device_tests reset && run_device_tests collect my_scenario.md --test-id=custom_001

    Later, replay with:
        run_device_tests run --mode=offline --test-artifact=custom_001
    """
    device_folder = ctx.obj["device_folder"]

    # Setup workspace WITHOUT reset first to check state
    global _workspace
    bench = Bench(REPO_ROOT, WORKSPACE_DIR / "logs")
    workspace = WorkspaceManager(WORKSPACE_DIR, REPO_ROOT, bench, device_folder)
    workspace.workspace_dir.mkdir(parents=True, exist_ok=True)
    workspace.log_dir.mkdir(parents=True, exist_ok=True)
    workspace._write_config()
    _workspace = workspace

    testdata_store = get_testdata_store()

    # Check if resuming
    resuming = _is_test_in_progress(workspace)

    if resuming:
        bench.header(f"RESUMING COLLECTION: {test_id}")
        bench.ok("Detected test in progress, skipping upload")

        # Ensure document exists
        if not workspace.test_doc.exists():
            shutil.copy(source_file, workspace.test_doc)
    else:
        bench.header(f"COLLECTING TESTDATA: {test_id}")

        # Fresh start - setup with the provided source file
        workspace.reset()
        workspace.setup_document(source_file)

        # Step 1: Initial sync (upload)
        ret, out, err = workspace.run_sync("Upload document")
        if ret != 0:
            bench.error("Failed to upload document")
            sys.exit(1)

        doc_uuid = workspace.get_document_uuid()
        if not doc_uuid:
            bench.error("Document UUID not found after sync")
            sys.exit(1)

        bench.observe(f"Document UUID: {doc_uuid}")

        # Step 2: User annotates
        from .harness.prompts import user_prompt

        if not user_prompt(
            "Annotate document",
            [
                f"Open '{workspace.device_folder}/document' on reMarkable",
                "Add annotations according to the document instructions",
                "Wait for cloud sync to complete",
            ],
        ):
            bench.warn("Cancelled by user")
            sys.exit(1)

    # Step 3: Sync to download annotations
    ret, out, err = workspace.run_sync("Download annotations")
    if ret != 0:
        bench.error("Failed to download annotations")
        bench.error(f"stderr: {err}")
        sys.exit(1)

    doc_uuid = workspace.get_document_uuid()
    if not doc_uuid:
        bench.error("Document UUID not found - is the document on the cloud?")
        sys.exit(1)

    bench.observe(f"Document UUID: {doc_uuid}")

    # Step 4: Collect .rm files
    rm_files_paths = workspace.get_cached_rm_files()
    if not rm_files_paths:
        bench.error("No .rm files found - did you add annotations?")
        sys.exit(1)

    # Build rm_files dict
    rm_files: dict[str, bytes] = {}
    page_uuids: list[str] = []
    for rm_path in sorted(rm_files_paths):
        page_uuid = rm_path.stem
        page_uuids.append(page_uuid)
        rm_files[page_uuid] = rm_path.read_bytes()

    bench.observe(f"Found {len(rm_files)} .rm file(s)")

    # Step 5: Save to testdata store
    save_path = testdata_store.save_artifacts(
        test_id=test_id,
        doc_uuid=doc_uuid,
        page_uuids=page_uuids,
        rm_files=rm_files,
        source_markdown=source_file,
        description=description or f"Collected from {source_file.name}",
    )

    bench.header("COLLECTION COMPLETE")
    click.echo(f"\nTestdata saved to: {save_path}")
    click.echo("\nTo replay this test:")
    click.echo(f"  run_device_tests run --mode=offline --test-artifact={test_id}")


@cli.command("export-curated")
@click.option(
    "--set-name",
    required=True,
    help="Name for the curated set",
)
@click.option(
    "--test-ids",
    required=True,
    help="Comma-separated list of test IDs to include",
)
@click.option(
    "--description",
    default="",
    help="Description of the curated set",
)
def export_curated(set_name: str, test_ids: str, description: str) -> None:
    """Export selected tests to a curated set.

    Curated sets are stable collections of tests for CI/regression testing.

    Example:

    \b
        run_device_tests export-curated \\
            --set-name=basic_annotations \\
            --test-ids=annotation_roundtrip_001,annotation_roundtrip_002 \\
            --description="Basic annotation tests for CI"
    """
    store = get_testdata_store()
    test_id_list = [t.strip() for t in test_ids.split(",")]

    # Validate all test IDs exist
    missing = []
    for test_id in test_id_list:
        if not store.test_exists(test_id):
            missing.append(test_id)

    if missing:
        click.echo(f"Error: Test artifacts not found: {', '.join(missing)}", err=True)
        click.echo("Run 'list-tests' to see available artifacts", err=True)
        sys.exit(1)

    # Export
    try:
        output_dir = store.export_curated_set(
            test_ids=test_id_list,
            set_name=set_name,
            description=description,
        )
        click.echo(f"Exported curated set to: {output_dir}")
        click.echo(f"  Tests: {len(test_id_list)}")
        click.echo(f"  Description: {description}")
    except Exception as e:
        click.echo(f"Error exporting: {e}", err=True)
        sys.exit(1)


@cli.command("migrate-legacy")
def migrate_legacy() -> None:
    """Migrate legacy testdata to new format.

    Converts the old ocr_handwriting testdata structure to the new
    collected testdata format so it can be used with offline replay.
    """
    import json

    store = get_testdata_store()
    legacy_dir = FIXTURES_DIR / "testdata" / "ocr_handwriting"

    if not legacy_dir.exists():
        click.echo("No legacy testdata found at: {legacy_dir}")
        return

    # Read old manifest
    old_manifest_path = legacy_dir / "manifest.json"
    if not old_manifest_path.exists():
        click.echo("No manifest.json found in legacy testdata")
        return

    old_manifest = json.loads(old_manifest_path.read_text())

    click.echo(f"Found legacy testdata: {old_manifest.get('description', 'unknown')}")
    click.echo(f"  Created: {old_manifest.get('created_at', 'unknown')}")
    click.echo(f"  .rm files: {old_manifest.get('num_rm_files', 0)}")

    # Collect .rm files
    rm_files: dict[str, bytes] = {}
    page_uuids: list[str] = []

    for rm_filename in old_manifest.get("rm_files", []):
        rm_path = legacy_dir / rm_filename
        if rm_path.exists():
            page_uuid = rm_path.stem
            page_uuids.append(page_uuid)
            rm_files[page_uuid] = rm_path.read_bytes()
            click.echo(f"  Found: {rm_filename}")

    if not rm_files:
        click.echo("No .rm files found")
        return

    # Find source markdown
    source_md = legacy_dir / "markdown" / "ocr_baseline.md"
    if not source_md.exists():
        source_md = FIXTURES_DIR / "ocr_baseline.md"

    if not source_md.exists():
        click.echo("Source markdown not found")
        return

    # Create new format testdata
    test_id = "ocr_handwriting_legacy"

    # Generate a placeholder doc_uuid (we don't have the original)
    doc_uuid = "legacy-" + page_uuids[0] if page_uuids else "legacy-unknown"

    save_path = store.save_artifacts(
        test_id=test_id,
        doc_uuid=doc_uuid,
        page_uuids=page_uuids,
        rm_files=rm_files,
        source_markdown=source_md,
        description=old_manifest.get("description", "Migrated legacy testdata"),
        metadata={
            "migrated_from": "ocr_handwriting",
            "original_created_at": old_manifest.get("created_at", "unknown"),
            "test_cases": json.dumps(old_manifest.get("test_cases", [])),
        },
    )

    click.echo(f"\nMigrated to: {save_path}")
    click.echo(f"Test ID: {test_id}")
    click.echo("\nTo replay:")
    click.echo("  uv run pytest tests/record_replay/test_offline_replay.py -v")


@cli.command()
@click.pass_context
def reset(ctx: click.Context) -> None:
    """Reset workspace state.

    Cleans up the workspace directory and cloud state.
    """
    device_folder = ctx.obj["device_folder"]
    bench, workspace = setup_workspace(device_folder)
    workspace.reset()
    click.echo("Workspace reset complete")


@cli.command()
@click.pass_context
def setup(ctx: click.Context) -> None:
    """Setup workspace only.

    Creates workspace directory and config without running tests.
    """
    device_folder = ctx.obj["device_folder"]
    bench, workspace = setup_workspace(device_folder)
    click.echo(f"Workspace ready at: {workspace.workspace_dir}")


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
