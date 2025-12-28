#!/usr/bin/env python3
"""Create renderer test case from device state.

Captures a single test case for the renderer test corpus by:
1. Downloading .rm file from cloud
2. Capturing thumbnail via SSH
3. Saving with metadata to test corpus

Usage:
    # Create a test case for a simple stroke
    uv run tools/analysis/create_renderer_test_case.py \
        --test-id 002_single_stroke_black \
        --doc-name "renderer-test" \
        --description "Single black stroke in middle of page"

    # Use first page only (default)
    uv run tools/analysis/create_renderer_test_case.py \
        --test-id 001_empty_page \
        --doc-name "empty-doc" \
        --description "Empty page with no strokes"

    # Capture specific page
    uv run tools/analysis/create_renderer_test_case.py \
        --test-id 003_multi_page \
        --doc-name "test-doc" \
        --page-index 1 \
        --description "Second page with annotations"

The test corpus is stored at: tests/fixtures/renderer_corpus/
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Add repo root to path for imports
REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# Import device capture functionality
from device_capture import (
    Colors,
    capture_thumbnails_via_ssh,
    download_rm_files,
    find_document,
    get_cloud_client,
    print_error,
    print_ok,
    print_step,
    print_warn,
)

# Default corpus directory
DEFAULT_CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "renderer_corpus"


def validate_test_id(test_id: str) -> bool:
    """Validate test ID format (NNN_description)."""
    if not test_id:
        return False

    # Should start with digits and underscore
    parts = test_id.split("_", 1)
    if len(parts) != 2:
        return False

    return parts[0].isdigit()


def create_test_case_metadata(
    test_id: str,
    doc_name: str,
    doc_uuid: str,
    page_uuid: str,
    description: str,
) -> dict:
    """Create metadata for a test case."""
    return {
        "test_id": test_id,
        "description": description,
        "source": {
            "doc_name": doc_name,
            "doc_uuid": doc_uuid,
            "page_uuid": page_uuid,
        },
        "captured_at": datetime.now().isoformat(),
        "expected_elements": {
            "strokes": "auto-detect",
            "highlights": "auto-detect",
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Create renderer test case from device state",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--test-id",
        required=True,
        help="Test case ID (e.g., 002_single_stroke_black)",
    )
    parser.add_argument(
        "--doc-name",
        required=True,
        help="Document name to capture (partial match)",
    )
    parser.add_argument(
        "--description",
        required=True,
        help="Description of what this test case validates",
    )
    parser.add_argument(
        "--page-index",
        type=int,
        default=0,
        help="Page index to capture (default: 0 = first page)",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=DEFAULT_CORPUS_DIR,
        help=f"Corpus directory (default: {DEFAULT_CORPUS_DIR})",
    )
    parser.add_argument(
        "--device-host",
        default="remarkable-ppm",
        help="Device hostname for SSH (default: remarkable-ppm)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing test case",
    )

    args = parser.parse_args()

    print(f"\n{Colors.BOLD}Create Renderer Test Case{Colors.END}")

    # Validate test ID format
    if not validate_test_id(args.test_id):
        print_error(f"Invalid test ID format: {args.test_id}")
        print("Expected format: NNN_description (e.g., 002_single_stroke_black)")
        sys.exit(1)

    # Check if test case already exists
    test_dir = args.corpus_dir / args.test_id
    if test_dir.exists() and not args.force:
        print_error(f"Test case already exists: {test_dir}")
        print("Use --force to overwrite")
        sys.exit(1)

    print(f"  Test ID: {args.test_id}")
    print(f"  Description: {args.description}")
    print(f"  Page index: {args.page_index}")

    # Initialize cloud clients
    print_step("Connecting to cloud")
    client, sync = get_cloud_client()
    print_ok("Connected")

    # Find document
    print_step("Finding document")
    doc_uuid, doc_name = find_document(sync, args.doc_name)
    print_ok(f"Found: '{doc_name}' ({doc_uuid})")

    # Download .rm files
    print_step("Downloading .rm files")
    rm_files = download_rm_files(sync, doc_uuid)

    if not rm_files:
        print_error("No .rm files found")
        sys.exit(1)

    # Get page by index
    page_uuids = sorted(rm_files.keys())
    if args.page_index >= len(page_uuids):
        print_error(f"Page index {args.page_index} out of range (have {len(page_uuids)} pages)")
        sys.exit(1)

    page_uuid = page_uuids[args.page_index]
    page_rm = rm_files[page_uuid]
    print_ok(f"Using page {args.page_index}: {page_uuid}")

    # Capture thumbnail
    print_step(f"Capturing thumbnail via SSH ({args.device_host})")
    thumbnails = capture_thumbnails_via_ssh(args.device_host, doc_uuid)

    if page_uuid not in thumbnails:
        print_error(f"Thumbnail not found for page {page_uuid}")
        print("Available thumbnails:", list(thumbnails.keys()))
        sys.exit(1)

    page_thumbnail = thumbnails[page_uuid]
    print_ok(f"Captured thumbnail ({len(page_thumbnail)} bytes)")

    # Create test case directory
    print_step(f"Creating test case: {test_dir}")
    test_dir.mkdir(parents=True, exist_ok=True)

    # Save .rm file
    rm_path = test_dir / "page.rm"
    rm_path.write_bytes(page_rm)
    print(f"  Saved: {rm_path}")

    # Save thumbnail
    thumb_path = test_dir / "device_thumbnail.png"
    thumb_path.write_bytes(page_thumbnail)
    print(f"  Saved: {thumb_path}")

    # Save metadata
    metadata = create_test_case_metadata(
        test_id=args.test_id,
        doc_name=doc_name,
        doc_uuid=doc_uuid,
        page_uuid=page_uuid,
        description=args.description,
    )
    metadata_path = test_dir / "description.json"
    metadata_path.write_text(json.dumps(metadata, indent=2))
    print(f"  Saved: {metadata_path}")

    print(f"\n{Colors.GREEN}Done!{Colors.END}")
    print(f"  Test case: {test_dir}")
    print(f"\nNext steps:")
    print(f"  1. Run rm_inspector.py on {rm_path} to verify content")
    print(f"  2. Run renderer tests: uv run pytest tests/rmlib/test_device_golden.py")


if __name__ == "__main__":
    main()
