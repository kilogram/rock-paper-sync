#!/usr/bin/env python3
"""Device iteration tool for rapid testing of annotation preservation.

Replays captured testdata to a real reMarkable device for visual verification
and device log analysis. Enables rapid iteration on annotation bugs without
re-running the full test suite.

Usage:
    # Run with default settings (cross_page_reanchor, trip 1 -> trip 2)
    uv run tools/analysis/device_iteration.py

    # Run specific test and trips
    uv run tools/analysis/device_iteration.py --test-id cross_page_reanchor --from-trip 1 --to-trip 2

    # Skip cleanup to leave document on device for inspection
    uv run tools/analysis/device_iteration.py --no-cleanup

    # Use custom device hostname
    uv run tools/analysis/device_iteration.py --device-host remarkable-ppm

Workflow:
    1. Load testdata from trips directory
    2. Restore vault to "from-trip" state (with annotations)
    3. Apply vault changes from "to-trip" (simulates markdown edits)
    4. Sync to real device (pushes regenerated document)
    5. Prompt user to open document on device
    6. Fetch device logs via SSH
    7. Cleanup (unless --no-cleanup)

The device logs are saved to /tmp/device_iteration_<timestamp>.log for analysis.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Add repo root to path for imports
REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests/record_replay"))

# Reuse existing test harness utilities
from harness.command import run_sync, run_unsync
from harness.output import Colors, print_error, print_info, print_ok, print_warn
from harness.testdata import TestdataStore, TripData


def print_step(msg: str) -> None:
    """Print step header."""
    print(f"\n{Colors.BOLD}> {msg}{Colors.END}")


# Default user config path (same as CLI and test harness)
DEFAULT_USER_CONFIG = Path.home() / ".config" / "rock-paper-sync" / "config.toml"


def load_trip(testdata_store: TestdataStore, test_id: str, trip_num: int | str) -> TripData:
    """Load a specific trip from testdata."""
    trips = testdata_store.load_trips(test_id)
    trip_name = str(trip_num)
    for trip in trips:
        if trip.trip_name == trip_name:
            return trip
    raise ValueError(f"Trip {trip_num} not found in test {test_id}")


def restore_vault(vault_dir: Path, trip: TripData) -> None:
    """Restore vault to trip state, preserving config and hidden dirs."""
    if not trip.vault_path or not trip.vault_path.exists():
        raise ValueError(f"Trip {trip.trip_name} has no vault data")

    # Clear vault (except hidden dirs and config)
    for item in vault_dir.iterdir():
        if item.name.startswith(".") or item.name == "config.toml" or item.name == "logs":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    # Copy vault files from trip
    for src in trip.vault_path.iterdir():
        dst = vault_dir / src.name
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def write_config(
    config_file: Path,
    workspace_dir: Path,
    device_folder: str,
    cloud_base_url: str,
) -> None:
    """Write config file for real device sync."""
    state_dir = workspace_dir / ".state"
    cache_dir = workspace_dir / ".cache"
    log_dir = workspace_dir / "logs"

    state_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    config = f"""# Device iteration test config
[cloud]
base_url = "{cloud_base_url}"

[paths]
state_database = "{state_dir}/state.db"
cache_dir = "{cache_dir}"

[logging]
level = "debug"
file = "{log_dir}/sync.log"

[layout]
# Lines per page is calculated from device geometry

[ocr]
enabled = false
provider = "none"

[[vaults]]
name = "device-iteration"
path = "{workspace_dir}"
remarkable_folder = "{device_folder}"
include_patterns = ["**/*.md"]
exclude_patterns = [".state/**", "logs/**", ".cache/**"]
"""
    config_file.write_text(config)


def inject_rm_files(
    config_file: Path,
    cloud_base_url: str,
    doc_uuid: str,
    rm_files: dict[str, bytes],
) -> None:
    """Inject .rm files into cloud using production sync code."""
    from rock_paper_sync.rm_cloud_client import RmCloudClient
    from rock_paper_sync.rm_cloud_sync import RmCloudSync

    print_info(f"Injecting {len(rm_files)} .rm files...")

    client = RmCloudClient(base_url=cloud_base_url)
    sync = RmCloudSync(base_url=cloud_base_url, client=client)

    pages = [(page_uuid, rm_data) for page_uuid, rm_data in rm_files.items()]

    sync.upload_document(
        doc_uuid=doc_uuid,
        document_name=f"Document {doc_uuid[:8]}",
        pages=pages,
        parent_uuid="",
    )
    print_ok(f"Injected {len(rm_files)} .rm files into document")


def get_doc_uuid(config_file: Path) -> str:
    """Get document UUID from state database."""
    from rock_paper_sync.config import load_config
    from rock_paper_sync.state import StateManager

    config = load_config(config_file)
    state = StateManager(config.sync.state_database)
    records = state.get_all_synced_files("device-iteration")
    state.close()

    if not records:
        raise ValueError("No documents found in state after sync")

    return records[0].remarkable_uuid


def fetch_device_logs(device_host: str, output_path: Path) -> None:
    """Fetch xochitl logs from device via SSH."""
    print_step(f"Fetching device logs from {device_host}")

    cmd = [
        "ssh",
        f"root@{device_host}",
        "journalctl", "-xb", "-u", "xochitl",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            output_path.write_text(result.stdout)
            print_ok(f"Saved logs to {output_path}")

            # Show relevant errors
            errors = [
                line for line in result.stdout.split("\n")
                if any(x in line for x in ["ERROR", "Unable to find", "left not found", "right not found"])
            ]
            if errors:
                print_warn("Found errors in device logs:")
                for err in errors[:10]:
                    print(f"    {err}")
            else:
                print_ok("No errors found in device logs")
        else:
            print_error(f"SSH failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        print_error("SSH timed out")
    except Exception as e:
        print_error(f"Failed to fetch logs: {e}")


def wait_for_user(prompt: str) -> None:
    """Wait for user to press Enter."""
    print(f"\n{Colors.YELLOW}{prompt}{Colors.END}")
    input("Press Enter to continue...")


def main():
    parser = argparse.ArgumentParser(
        description="Device iteration tool for testing annotation preservation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--test-id",
        default="cross_page_reanchor",
        help="Test ID to load (default: cross_page_reanchor)",
    )
    parser.add_argument(
        "--from-trip",
        type=int,
        default=1,
        help="Trip number to restore annotations from (default: 1)",
    )
    parser.add_argument(
        "--to-trip",
        type=int,
        default=2,
        help="Trip number to apply vault changes from (default: 2)",
    )
    parser.add_argument(
        "--device-host",
        default="remarkable-ppm",
        help="Device hostname for SSH (default: remarkable-ppm)",
    )
    parser.add_argument(
        "--device-folder",
        default="DeviceIteration",
        help="Folder name on device (default: DeviceIteration)",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip cleanup to leave document on device",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Workspace directory (default: temp dir)",
    )
    parser.add_argument(
        "--user-config",
        type=Path,
        default=DEFAULT_USER_CONFIG,
        help="User config file to read cloud settings from",
    )

    args = parser.parse_args()

    # Load user config for cloud settings (using production config loader)
    from rock_paper_sync.config import load_config

    if not args.user_config.exists():
        print_error(f"User config not found: {args.user_config}")
        print_error("Run: uv run rock-paper-sync init")
        sys.exit(1)

    user_config = load_config(args.user_config)
    cloud_base_url = user_config.cloud.base_url

    # Set up paths
    testdata_dir = REPO_ROOT / "tests/record_replay/testdata"
    testdata_store = TestdataStore(testdata_dir)

    # Create workspace
    if args.workspace:
        workspace_dir = args.workspace
        workspace_dir.mkdir(parents=True, exist_ok=True)
        cleanup_workspace = False
    else:
        workspace_dir = Path(tempfile.mkdtemp(prefix="device_iteration_"))
        cleanup_workspace = True

    config_file = workspace_dir / "config.toml"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_output = Path(f"/tmp/device_iteration_{timestamp}.log")

    print(f"\n{Colors.BOLD}Device Iteration Tool{Colors.END}")
    print(f"  Test ID: {args.test_id}")
    print(f"  From trip: {args.from_trip} -> To trip: {args.to_trip}")
    print(f"  Device: {args.device_host}")
    print(f"  Workspace: {workspace_dir}")
    print(f"  Cloud: {cloud_base_url}")

    try:
        # Step 1: Load trips
        print_step("Loading testdata")
        from_trip = load_trip(testdata_store, args.test_id, args.from_trip)
        to_trip = load_trip(testdata_store, args.test_id, args.to_trip)
        print_ok(f"Loaded trips {args.from_trip} and {args.to_trip}")

        # Step 2: Set up workspace and initial config
        print_step("Setting up workspace")
        write_config(config_file, workspace_dir, args.device_folder, cloud_base_url)
        restore_vault(workspace_dir, from_trip)
        print_ok("Workspace ready with from-trip vault")

        # Step 3: Initial sync to upload document
        run_sync(config_file, "Initial upload")

        # Step 4: Inject annotations from from-trip
        print_step("Injecting annotations")
        doc_uuid = get_doc_uuid(config_file)
        if not from_trip.annotations or not from_trip.annotations.rm_files:
            raise ValueError(f"Trip {args.from_trip} has no annotations to inject")
        inject_rm_files(config_file, cloud_base_url, doc_uuid, from_trip.annotations.rm_files)

        # Step 5: Download annotations back
        run_sync(config_file, "Download annotations")

        # Step 6: Apply to-trip vault changes (simulates markdown edits)
        print_step("Applying vault changes from to-trip")
        restore_vault(workspace_dir, to_trip)
        print_ok("Vault updated to to-trip state")

        # Step 7: Sync regenerated document to device
        run_sync(config_file, "Sync regenerated document")

        # Step 8: Prompt user
        wait_for_user(
            f"Open the document on your device ({args.device_host}).\n"
            "Check for missing strokes or misaligned highlights."
        )

        # Step 9: Fetch device logs
        fetch_device_logs(args.device_host, log_output)

        # Step 10: Cleanup
        if not args.no_cleanup:
            print_step("Cleaning up")
            run_unsync(config_file)
            print_ok("Cleanup complete")
        else:
            print_warn("Skipping cleanup (--no-cleanup)")

    except KeyboardInterrupt:
        print_warn("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print_error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if cleanup_workspace and workspace_dir.exists():
            shutil.rmtree(workspace_dir, ignore_errors=True)

    print(f"\n{Colors.GREEN}Done!{Colors.END}")
    print(f"  Device logs: {log_output}")


if __name__ == "__main__":
    main()
