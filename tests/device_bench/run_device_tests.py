#!/usr/bin/env python3
"""Device test runner for interactive device testing.

This script provides the command-line interface for running device tests.
For programmatic testing, use pytest with the device marker.

Usage:
    # Run all tests
    uv run python -m tests.device_bench.run_device_tests

    # Run specific test
    uv run python -m tests.device_bench.run_device_tests --test annotation-roundtrip

    # Run with cleanup disabled
    uv run python -m tests.device_bench.run_device_tests --no-cleanup

    # Reset workspace only
    uv run python -m tests.device_bench.run_device_tests --reset

    # Extract testdata for automated testing
    uv run python -m tests.device_bench.run_device_tests --extract-testdata
"""

import argparse
import atexit
import signal
import sys
from pathlib import Path

from .harness import Bench, WorkspaceManager
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
_cleanup_on_exit = False
_workspace: WorkspaceManager | None = None


def _signal_handler(signum, frame):
    """Handle interrupt signals."""
    global _cleanup_on_exit, _workspace
    if _cleanup_on_exit and _workspace:
        print("\nInterrupted - cleaning up...")
        _workspace.cleanup()
    sys.exit(1)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def setup_workspace() -> tuple[Bench, WorkspaceManager]:
    """Create and setup workspace."""
    global _workspace

    bench = Bench(REPO_ROOT, WORKSPACE_DIR / "logs")
    workspace = WorkspaceManager(WORKSPACE_DIR, REPO_ROOT, bench)
    workspace.setup()
    _workspace = workspace

    return bench, workspace


def run_test(
    test_name: str,
    bench: Bench,
    workspace: WorkspaceManager,
    cleanup: bool = True,
) -> bool:
    """Run a single test.

    Args:
        test_name: Name of test to run
        bench: Bench utilities
        workspace: Workspace manager
        cleanup: Whether to cleanup after test

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

    # Create and run test
    test = test_class(workspace, bench)
    result = test.run()

    # Save result
    bench.save_result(result)

    # Cleanup if requested and test passed (or cleanup_on_failure)
    if cleanup:
        if result.success or test.cleanup_on_failure:
            workspace.cleanup()
        else:
            bench.warn(f"Workspace preserved at: {workspace.workspace_dir}")
            bench.info("Run with --reset to cleanup manually")

    return result.success


def run_all_tests(
    bench: Bench,
    workspace: WorkspaceManager,
    cleanup: bool = True,
) -> list[tuple[str, bool]]:
    """Run all tests.

    Args:
        bench: Bench utilities
        workspace: Workspace manager
        cleanup: Whether to cleanup after all tests

    Returns:
        List of (test_name, success) tuples
    """
    bench.header("DEVICE TEST BENCH")

    # Display test info
    print("""
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
  - reMarkable device connected
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

        # Run test
        test = test_class(workspace, bench)
        result = test.run()
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


def extract_testdata(bench: Bench, workspace: WorkspaceManager) -> bool:
    """Extract .rm files with handwriting as testdata.

    This workflow:
    1. Syncs document to device
    2. User writes handwriting
    3. Syncs back
    4. Copies .rm files to testdata directory
    """
    import json
    import shutil
    from datetime import datetime

    bench.header("TESTDATA EXTRACTION")

    # Setup
    workspace.reset()
    workspace.setup_document(OCR_BASELINE_DOC)

    # Step 1: Initial sync
    ret, out, err = workspace.run_sync("Initial sync")
    if ret != 0:
        return False

    # Step 2: User writes
    from .harness.prompts import user_prompt
    if not user_prompt("Write test handwriting", [
        f"Open '{workspace.device_folder}/document' on reMarkable",
        "Write in ALL test sections (Test 1-4)",
        "Use clear, readable handwriting",
        "Highlight each gap where you wrote",
        "Wait for cloud sync to complete",
    ]):
        return False

    # Step 3: Sync to download
    ret, out, err = workspace.run_sync("Download annotations")
    if ret != 0:
        return False

    # Step 4: Find and copy .rm files
    doc_uuid = workspace.get_document_uuid()
    if not doc_uuid:
        bench.error("Document not found in sync state")
        return False

    bench.observe(f"Document UUID: {doc_uuid}")

    rm_files = workspace.get_cached_rm_files()
    if not rm_files:
        bench.error("No .rm files found in cache")
        return False

    bench.observe(f"Found {len(rm_files)} .rm file(s)")

    # Create testdata directory
    testdata_dir = FIXTURES_DIR / "testdata" / "ocr_handwriting"
    testdata_dir.mkdir(parents=True, exist_ok=True)
    markdown_dir = testdata_dir / "markdown"
    markdown_dir.mkdir(exist_ok=True)

    # Copy .rm files
    for rm_file in rm_files:
        dest = testdata_dir / rm_file.name
        shutil.copy(rm_file, dest)
        bench.observe(f"Copied: {rm_file.name}")

    # Copy markdown source
    shutil.copy(workspace.test_doc, markdown_dir / "ocr_baseline.md")
    bench.observe("Copied markdown source")

    # Create manifest
    manifest = {
        "created_at": datetime.now().isoformat(),
        "source_document": "ocr_baseline.md",
        "num_rm_files": len(rm_files),
        "rm_files": [f.name for f in rm_files],
        "description": "OCR test handwriting samples",
        "test_cases": [
            {"section": "Test 1", "expected": "hello"},
            {"section": "Test 2", "expected": "2025"},
            {"section": "Test 3", "expected": "quick test"},
            {"section": "Test 4", "expected": "Code 42"},
        ]
    }

    manifest_path = testdata_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    bench.observe(f"Created manifest: {manifest_path}")

    bench.header("EXTRACTION COMPLETE")
    print(f"\nTestdata saved to: {testdata_dir}")
    print("\nNext steps:")
    print("  1. Review extracted .rm files")
    print("  2. Run: uv run pytest tests/test_ocr_testdata.py")
    print("  3. Commit testdata to repository")

    return True


def main():
    """Main entry point."""
    global _cleanup_on_exit

    parser = argparse.ArgumentParser(description="Device Test Runner")
    parser.add_argument(
        "--test",
        choices=list(TESTS_BY_NAME.keys()),
        help="Run specific test",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset workspace state",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Setup workspace only",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Cleanup workspace on exit",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Don't cleanup workspace after test",
    )
    parser.add_argument(
        "--extract-testdata",
        action="store_true",
        help="Extract .rm files with handwriting for automated testing",
    )

    args = parser.parse_args()
    _cleanup_on_exit = args.cleanup

    # Setup
    bench, workspace = setup_workspace()

    if args.reset:
        workspace.reset()
        sys.exit(0)

    if args.setup:
        sys.exit(0)

    if args.extract_testdata:
        success = extract_testdata(bench, workspace)
        sys.exit(0 if success else 1)

    if args.test:
        cleanup = not args.no_cleanup
        success = run_test(args.test, bench, workspace, cleanup)
        sys.exit(0 if success else 1)

    # Run all tests
    cleanup = not args.no_cleanup
    results = run_all_tests(bench, workspace, cleanup)
    passed = sum(1 for _, s in results if s)
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
