#!/usr/bin/env python3
"""Compare .rm files visually and save debug images.

This tool renders .rm files to PNG and compares them using perceptual hashing.
Useful for reviewing stroke positions and visual differences.

Prerequisites:
    Install rmc via pipx (not uv, due to rmscene version conflict):
        pipx install rmc

Usage:
    # Compare two .rm files
    uv run python tools/analysis/compare_rm_visual.py test.rm golden.rm

    # Compare all .rm files in two directories
    uv run python tools/analysis/compare_rm_visual.py test_dir/ golden_dir/

    # Compare testdata trips
    uv run python tools/analysis/compare_rm_visual.py --testdata cross_page_reanchor --trip 1

    # Specify output directory
    uv run python tools/analysis/compare_rm_visual.py test.rm golden.rm -o output/

Output:
    Creates debug images in the output directory:
    - {page}_golden.png         Full page render of golden
    - {page}_test.png           Full page render of test
    - {page}_stroke{N}_golden.png  Cropped region around stroke N (golden)
    - {page}_stroke{N}_test.png    Cropped region around stroke N (test)
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def compare_rm_files(test_path: Path, golden_path: Path, output_dir: Path) -> int:
    """Compare two .rm files and save debug images."""
    from tests.record_replay.harness.visual_comparison import (
        check_rmc_installed,
        compare_rm_files_visually,
        print_visual_comparison,
        save_comparison_debug_images,
    )

    if not check_rmc_installed():
        print("Error: rmc not installed. Install with: pipx install rmc", file=sys.stderr)
        return 1

    test_rm = {test_path.stem: test_path.read_bytes()}
    golden_rm = {golden_path.stem: golden_path.read_bytes()}

    # Run comparison
    result = compare_rm_files_visually(test_rm, golden_rm)
    print_visual_comparison(test_rm, golden_rm)

    # Save debug images
    saved = save_comparison_debug_images(test_rm, golden_rm, output_dir)

    print(f"\nSaved {len(saved)} debug images to: {output_dir}")
    for p in saved:
        print(f"  - {p.name}")

    return 0


def compare_directories(test_dir: Path, golden_dir: Path, output_dir: Path) -> int:
    """Compare all .rm files in two directories."""
    from tests.record_replay.harness.visual_comparison import (
        check_rmc_installed,
        compare_rm_files_visually,
        print_visual_comparison,
        save_comparison_debug_images,
    )

    if not check_rmc_installed():
        print("Error: rmc not installed. Install with: pipx install rmc", file=sys.stderr)
        return 1

    # Load all .rm files
    test_rm = {}
    for rm_path in test_dir.glob("*.rm"):
        test_rm[rm_path.stem] = rm_path.read_bytes()

    golden_rm = {}
    for rm_path in golden_dir.glob("*.rm"):
        golden_rm[rm_path.stem] = rm_path.read_bytes()

    if not test_rm:
        print(f"Error: No .rm files found in {test_dir}", file=sys.stderr)
        return 1

    if not golden_rm:
        print(f"Error: No .rm files found in {golden_dir}", file=sys.stderr)
        return 1

    print(f"Comparing {len(test_rm)} test files vs {len(golden_rm)} golden files")

    # Run comparison
    result = compare_rm_files_visually(test_rm, golden_rm)
    print_visual_comparison(test_rm, golden_rm)

    # Save debug images
    saved = save_comparison_debug_images(test_rm, golden_rm, output_dir)

    print(f"\nSaved {len(saved)} debug images to: {output_dir}")

    return 0


def compare_testdata(
    test_id: str,
    trip_number: int,
    output_dir: Path,
    use_annotations: bool = False,
    mode: str = "offline",
) -> int:
    """Compare a testdata trip against golden.

    By default compares uploaded_rm (what our code generated) against golden.
    This is the primary validation use case.

    Args:
        test_id: Test identifier
        trip_number: Trip number to compare
        output_dir: Output directory for debug images
        use_annotations: If True, use annotations (user created on device) instead
                        of uploaded_rm (our generated output). Rarely needed.
        mode: "offline" or "online" - which diagnostic directory to use
    """
    import shutil

    from tests.record_replay.harness.testdata import TestdataStore
    from tests.record_replay.harness.visual_comparison import (
        check_rmc_installed,
        compare_rm_files_visually,
        print_visual_comparison,
        save_comparison_debug_images,
    )

    if not check_rmc_installed():
        print("Error: rmc not installed. Install with: pipx install rmc", file=sys.stderr)
        return 1

    # Find testdata directory
    testdata_dir = Path(__file__).parent.parent.parent / "tests" / "record_replay" / "testdata"
    store = TestdataStore(testdata_dir)

    if not store.has_trips(test_id):
        print(f"Error: No trips found for test '{test_id}'", file=sys.stderr)
        return 1

    # Load trip and golden
    trip = store.get_trip(test_id, trip_number)
    golden = store.get_golden(test_id)

    if not trip:
        print(f"Error: Trip {trip_number} not found for test '{test_id}'", file=sys.stderr)
        return 1

    if not golden:
        print(f"Error: No golden data found for test '{test_id}'", file=sys.stderr)
        return 1

    # Get test .rm files - default to uploaded_rm (our generated output)
    if use_annotations:
        # Annotations: user-created on device (rare use case)
        if not trip.annotations or not trip.annotations.rm_files:
            print(f"Error: Trip {trip_number} has no annotations", file=sys.stderr)
            return 1
        test_rm = trip.annotations.rm_files
        source_desc = "annotations (user created on device)"
    else:
        # Default: uploaded_rm (what we generated and uploaded)
        if not trip.diagnostic_path:
            print(f"Error: Trip {trip_number} has no diagnostic data", file=sys.stderr)
            return 1

        # Try mode-specific path first, then fall back to legacy path
        uploaded_dir = trip.diagnostic_path / mode / "uploaded_rm" / "rm_files"
        if not uploaded_dir.exists():
            # Fall back to legacy path (no mode prefix)
            uploaded_dir = trip.diagnostic_path / "uploaded_rm" / "rm_files"

        if not uploaded_dir.exists():
            print(f"Error: No uploaded_rm found for {mode} mode", file=sys.stderr)
            print(f"  Checked: {trip.diagnostic_path / mode / 'uploaded_rm' / 'rm_files'}", file=sys.stderr)
            print("  (Run the test in offline mode to capture diagnostic files)", file=sys.stderr)
            return 1

        test_rm = {}
        for rm_file in uploaded_dir.glob("*.rm"):
            test_rm[rm_file.stem] = rm_file.read_bytes()

        if not test_rm:
            print(f"Error: No .rm files in {uploaded_dir}", file=sys.stderr)
            return 1

        source_desc = f"{mode}/uploaded_rm (our generated output)"

    if not golden.annotations or not golden.annotations.rm_files:
        print(f"Error: Golden has no annotations", file=sys.stderr)
        return 1

    golden_rm = golden.annotations.rm_files

    print(f"Comparing {test_id} trip {trip_number} vs golden")
    print(f"  Test source: {source_desc}")
    print(f"  Test: {len(test_rm)} .rm files")
    print(f"  Golden: {len(golden_rm)} .rm files")

    # Run comparison
    result = compare_rm_files_visually(test_rm, golden_rm)
    print_visual_comparison(test_rm, golden_rm)

    # Clear and save debug images
    suffix = "_annotations" if use_annotations else ""
    dest_dir = output_dir / f"{test_id}{suffix}"
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    saved = save_comparison_debug_images(
        test_rm, golden_rm, output_dir, test_name=f"{test_id}{suffix}"
    )

    print(f"\nSaved {len(saved)} debug images to: {dest_dir}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare .rm files visually and save debug images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "test",
        nargs="?",
        type=Path,
        help="Test .rm file or directory",
    )
    parser.add_argument(
        "golden",
        nargs="?",
        type=Path,
        help="Golden .rm file or directory",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output directory for debug images (default: tests/record_replay/debug_images/)",
    )
    parser.add_argument(
        "--testdata",
        type=str,
        help="Test ID to compare from testdata (e.g., cross_page_reanchor)",
    )
    parser.add_argument(
        "--trip",
        type=int,
        default=1,
        help="Trip number to compare against golden (default: 1)",
    )
    parser.add_argument(
        "--use-annotations",
        action="store_true",
        help="Compare annotations (user created on device) instead of uploaded_rm (rarely needed)",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Use online diagnostic directory instead of offline (default)",
    )

    args = parser.parse_args()

    # Determine output directory
    if args.output:
        output_dir = args.output
    else:
        # Default to tests/record_replay/debug_images/
        output_dir = (
            Path(__file__).parent.parent.parent
            / "tests"
            / "record_replay"
            / "debug_images"
        )

    # Add timestamp subdirectory for uniqueness
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.testdata:
        mode = "online" if args.online else "offline"
        return compare_testdata(
            args.testdata, args.trip, output_dir, args.use_annotations, mode
        )

    if not args.test or not args.golden:
        parser.print_help()
        print("\nError: Must provide test and golden paths, or use --testdata", file=sys.stderr)
        return 1

    if not args.test.exists():
        print(f"Error: Test path not found: {args.test}", file=sys.stderr)
        return 1

    if not args.golden.exists():
        print(f"Error: Golden path not found: {args.golden}", file=sys.stderr)
        return 1

    if args.test.is_file() and args.golden.is_file():
        return compare_rm_files(args.test, args.golden, output_dir / timestamp)
    elif args.test.is_dir() and args.golden.is_dir():
        return compare_directories(args.test, args.golden, output_dir / timestamp)
    else:
        print("Error: Both paths must be files or both must be directories", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
