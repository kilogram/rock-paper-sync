#!/usr/bin/env python3
"""Device capture tool for downloading .rm files, thumbnails, and logs.

Captures complete device state for a document to create renderer test cases.
Builds on device_iteration.py patterns for SSH communication.

Usage:
    # List documents on device
    uv run tools/analysis/device_capture.py --list

    # Capture document by name (downloads .rm files + thumbnails)
    uv run tools/analysis/device_capture.py --doc-name "device-native-ref" -o tests/fixtures/captures/

    # Also capture xochitl logs
    uv run tools/analysis/device_capture.py --doc-name "test-doc" --capture-logs -o /tmp/capture/

    # Use custom device hostname
    uv run tools/analysis/device_capture.py --doc-name "test" --device-host remarkable-ppm

Requires:
    - SSH access to device (root@{device-host})
    - rmfakecloud configured for cloud API access
"""

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Add repo root to path for imports
REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from rock_paper_sync.rm_cloud_client import RmCloudClient
from rock_paper_sync.sync_v3 import SyncV3Client

# Default config path (same as CLI and test harness)
DEFAULT_USER_CONFIG = Path.home() / ".config" / "rock-paper-sync" / "config.toml"


@dataclass
class DeviceCapture:
    """Captured device state for a document."""

    doc_uuid: str
    doc_name: str
    page_uuids: list[str]
    rm_files: dict[str, bytes]  # page_uuid -> .rm bytes
    thumbnails: dict[str, bytes]  # page_uuid -> PNG bytes
    logs: str | None  # xochitl logs if captured
    timestamp: str


class Colors:
    """ANSI color codes for terminal output."""

    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    END = "\033[0m"


def print_ok(msg: str) -> None:
    print(f"{Colors.GREEN}OK{Colors.END} {msg}")


def print_warn(msg: str) -> None:
    print(f"{Colors.YELLOW}WARN{Colors.END} {msg}")


def print_error(msg: str) -> None:
    print(f"{Colors.RED}ERROR{Colors.END} {msg}")


def print_step(msg: str) -> None:
    print(f"\n{Colors.BOLD}> {msg}{Colors.END}")


def get_cloud_client() -> tuple[RmCloudClient, SyncV3Client]:
    """Initialize cloud clients using user config."""
    from rock_paper_sync.config import load_config

    if not DEFAULT_USER_CONFIG.exists():
        print_error(f"User config not found: {DEFAULT_USER_CONFIG}")
        print_error("Run: uv run rock-paper-sync init")
        sys.exit(1)

    config = load_config(DEFAULT_USER_CONFIG)
    client = RmCloudClient(base_url=config.cloud.base_url)

    if not client.is_registered():
        print_error("Device not registered. Run: uv run rock-paper-sync register <code>")
        sys.exit(1)

    user_token = client.get_user_token()
    sync = SyncV3Client(base_url=config.cloud.base_url, device_token=user_token)

    return client, sync


def list_documents(sync: SyncV3Client) -> dict[str, str]:
    """Get mapping of doc_uuid -> visible_name for all documents."""
    root_docs = sync.get_root_documents()
    doc_names = {}

    for entry in root_docs:
        if entry.type == "80000000":  # Document type
            doc_index = sync.download_blob(entry.hash)
            doc_files = sync.parse_index(doc_index)

            for file_entry in doc_files:
                if file_entry.entry_name.endswith(".metadata"):
                    metadata_bytes = sync.download_blob(file_entry.hash)
                    metadata = json.loads(metadata_bytes)
                    doc_names[entry.entry_name] = metadata.get("visibleName", "Unknown")
                    break

    return doc_names


def find_document(sync: SyncV3Client, name: str) -> tuple[str, str]:
    """Find document UUID by name (partial match)."""
    doc_names = list_documents(sync)

    matches = [(uuid, doc_name) for uuid, doc_name in doc_names.items() if name.lower() in doc_name.lower()]

    if not matches:
        print_error(f"No documents matching '{name}'")
        print("\nAvailable documents:")
        for uuid, doc_name in sorted(doc_names.items(), key=lambda x: x[1]):
            print(f"  {doc_name}: {uuid}")
        sys.exit(1)

    if len(matches) > 1:
        print_error(f"Multiple matches for '{name}':")
        for uuid, doc_name in matches:
            print(f"  {doc_name}: {uuid}")
        print("\nBe more specific.")
        sys.exit(1)

    return matches[0]


def download_rm_files(sync: SyncV3Client, doc_uuid: str) -> dict[str, bytes]:
    """Download all .rm files for a document from cloud."""
    root_docs = sync.get_root_documents()
    rm_files = {}

    for entry in root_docs:
        if entry.entry_name == doc_uuid:
            doc_index = sync.download_blob(entry.hash)
            doc_files = sync.parse_index(doc_index)

            for file_entry in doc_files:
                if file_entry.entry_name.endswith(".rm"):
                    content = sync.download_blob(file_entry.hash)
                    # Extract page UUID from path (e.g., "uuid/pages/page_uuid.rm")
                    page_uuid = file_entry.entry_name.split("/")[-1].replace(".rm", "")
                    rm_files[page_uuid] = content
                    print(f"  Downloaded .rm: {page_uuid} ({len(content)} bytes)")

            break

    return rm_files


def capture_thumbnails_via_ssh(
    device_host: str,
    doc_uuid: str,
) -> dict[str, bytes]:
    """Capture thumbnails from device via SSH.

    reMarkable stores thumbnails at:
    /home/root/.local/share/remarkable/xochitl/{doc_uuid}.thumbnails/{page_uuid}.png
    """
    thumbnails = {}
    thumb_dir = f"/home/root/.local/share/remarkable/xochitl/{doc_uuid}.thumbnails"

    # First, list available thumbnails
    list_cmd = ["ssh", f"root@{device_host}", f"ls {thumb_dir}/*.png 2>/dev/null"]

    try:
        result = subprocess.run(list_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print_warn(f"No thumbnails found at {thumb_dir}")
            return thumbnails

        thumb_files = [f.strip() for f in result.stdout.split("\n") if f.strip()]
        print(f"  Found {len(thumb_files)} thumbnails on device")

        for thumb_path in thumb_files:
            page_uuid = Path(thumb_path).stem

            # Download each thumbnail
            cat_cmd = ["ssh", f"root@{device_host}", f"cat {thumb_path}"]
            result = subprocess.run(cat_cmd, capture_output=True, timeout=30)

            if result.returncode == 0:
                thumbnails[page_uuid] = result.stdout
                print(f"  Downloaded thumbnail: {page_uuid} ({len(result.stdout)} bytes)")
            else:
                print_warn(f"  Failed to download thumbnail: {page_uuid}")

    except subprocess.TimeoutExpired:
        print_error("SSH timed out")
    except Exception as e:
        print_error(f"Failed to capture thumbnails: {e}")

    return thumbnails


def capture_logs_via_ssh(device_host: str) -> str | None:
    """Capture xochitl logs from device via SSH."""
    cmd = [
        "ssh",
        f"root@{device_host}",
        "journalctl",
        "-xb",
        "-u",
        "xochitl",
        "--no-pager",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return result.stdout
        else:
            print_warn(f"Failed to capture logs: {result.stderr}")
            return None
    except subprocess.TimeoutExpired:
        print_error("SSH timed out while capturing logs")
        return None
    except Exception as e:
        print_error(f"Failed to capture logs: {e}")
        return None


def save_capture(capture: DeviceCapture, output_dir: Path) -> None:
    """Save captured data to output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save metadata
    metadata = {
        "doc_uuid": capture.doc_uuid,
        "doc_name": capture.doc_name,
        "page_uuids": capture.page_uuids,
        "timestamp": capture.timestamp,
    }
    metadata_path = output_dir / "capture_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2))
    print(f"  Saved metadata: {metadata_path}")

    # Save .rm files
    for page_uuid, rm_bytes in capture.rm_files.items():
        rm_path = output_dir / f"{page_uuid}.rm"
        rm_path.write_bytes(rm_bytes)
        print(f"  Saved .rm file: {rm_path}")

    # Save thumbnails
    thumb_dir = output_dir / "thumbnails"
    if capture.thumbnails:
        thumb_dir.mkdir(exist_ok=True)
        for page_uuid, png_bytes in capture.thumbnails.items():
            thumb_path = thumb_dir / f"{page_uuid}.png"
            thumb_path.write_bytes(png_bytes)
            print(f"  Saved thumbnail: {thumb_path}")

    # Save logs
    if capture.logs:
        logs_path = output_dir / "xochitl.log"
        logs_path.write_text(capture.logs)
        print(f"  Saved logs: {logs_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Capture device state for renderer testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--doc-name",
        help="Document name to capture (partial match)",
    )
    parser.add_argument(
        "--doc-uuid",
        help="Document UUID to capture (exact)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all documents and exit",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output directory (default: temp dir)",
    )
    parser.add_argument(
        "--device-host",
        default="remarkable-ppm",
        help="Device hostname for SSH (default: remarkable-ppm)",
    )
    parser.add_argument(
        "--capture-logs",
        action="store_true",
        help="Also capture xochitl logs",
    )
    parser.add_argument(
        "--skip-thumbnails",
        action="store_true",
        help="Skip thumbnail capture via SSH",
    )

    args = parser.parse_args()

    print(f"\n{Colors.BOLD}Device Capture Tool{Colors.END}")

    # Initialize cloud clients
    print_step("Connecting to cloud")
    client, sync = get_cloud_client()
    print_ok("Connected")

    # Handle --list
    if args.list:
        print_step("Listing documents")
        doc_names = list_documents(sync)
        print(f"\nFound {len(doc_names)} documents:\n")
        for uuid, name in sorted(doc_names.items(), key=lambda x: x[1]):
            print(f"  {name}: {uuid}")
        return

    # Require --doc-name or --doc-uuid
    if not args.doc_name and not args.doc_uuid:
        print_error("Either --doc-name or --doc-uuid required")
        print("Use --list to see available documents")
        sys.exit(1)

    # Find document
    print_step("Finding document")
    if args.doc_uuid:
        doc_uuid = args.doc_uuid
        doc_names = list_documents(sync)
        doc_name = doc_names.get(doc_uuid, "Unknown")
    else:
        doc_uuid, doc_name = find_document(sync, args.doc_name)

    print_ok(f"Found: '{doc_name}' ({doc_uuid})")

    # Download .rm files from cloud
    print_step("Downloading .rm files from cloud")
    rm_files = download_rm_files(sync, doc_uuid)

    if not rm_files:
        print_error("No .rm files found")
        sys.exit(1)

    print_ok(f"Downloaded {len(rm_files)} .rm files")

    # Capture thumbnails via SSH
    thumbnails = {}
    if not args.skip_thumbnails:
        print_step(f"Capturing thumbnails via SSH ({args.device_host})")
        thumbnails = capture_thumbnails_via_ssh(args.device_host, doc_uuid)

        if thumbnails:
            print_ok(f"Captured {len(thumbnails)} thumbnails")
        else:
            print_warn("No thumbnails captured")
    else:
        print_step("Skipping thumbnail capture (--skip-thumbnails)")

    # Capture logs
    logs = None
    if args.capture_logs:
        print_step(f"Capturing logs via SSH ({args.device_host})")
        logs = capture_logs_via_ssh(args.device_host)

        if logs:
            print_ok("Captured logs")
        else:
            print_warn("No logs captured")

    # Create capture object
    capture = DeviceCapture(
        doc_uuid=doc_uuid,
        doc_name=doc_name,
        page_uuids=list(rm_files.keys()),
        rm_files=rm_files,
        thumbnails=thumbnails,
        logs=logs,
        timestamp=datetime.now().isoformat(),
    )

    # Determine output directory
    if args.output:
        output_dir = args.output
    else:
        output_dir = Path(tempfile.mkdtemp(prefix=f"capture_{doc_name.replace(' ', '_')}_"))

    # Save capture
    print_step(f"Saving to {output_dir}")
    save_capture(capture, output_dir)

    print(f"\n{Colors.GREEN}Done!{Colors.END}")
    print(f"  Output: {output_dir}")
    print(f"  .rm files: {len(rm_files)}")
    print(f"  Thumbnails: {len(thumbnails)}")
    if logs:
        print(f"  Logs: captured")


if __name__ == "__main__":
    main()
