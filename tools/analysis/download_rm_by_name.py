#!/usr/bin/env python3
"""Download .rm files from rmfakecloud by document name."""

import argparse
import json
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from rock_paper_sync.config import load_config
from rock_paper_sync.rm_cloud_client import RmCloudClient
from rock_paper_sync.sync_v3 import SyncV3Client


def main():
    parser = argparse.ArgumentParser(description="Download .rm files by document name")
    parser.add_argument("name", help="Document name to search for (partial match)")
    parser.add_argument("-o", "--output", default=".", help="Output directory")
    parser.add_argument("--list", action="store_true", help="List all documents")
    args = parser.parse_args()

    # Load config to get base_url
    config_path = Path.home() / ".config" / "rock-paper-sync" / "config.toml"
    config = load_config(config_path)
    base_url = config.cloud.base_url
    print(f"Using cloud at: {base_url}")

    # Initialize client with configured base_url
    client = RmCloudClient(base_url=base_url)
    if not client.is_registered():
        print("Error: Device not registered. Run: rock-paper-sync register <code>")
        sys.exit(1)

    user_token = client.get_user_token()
    sync = SyncV3Client(base_url=base_url, device_token=user_token)

    # Get all documents
    root_docs = sync.get_root_documents()
    print(f"Found {len(root_docs)} documents in cloud")

    # Build name mapping by downloading metadata
    doc_names = {}
    for entry in root_docs:
        if entry.type == "80000000":  # Document type
            doc_index = sync.download_blob(entry.hash)
            doc_files = sync.parse_index(doc_index)

            # Find and download metadata
            for file_entry in doc_files:
                if file_entry.entry_name.endswith(".metadata"):
                    metadata_bytes = sync.download_blob(file_entry.hash)
                    metadata = json.loads(metadata_bytes)
                    doc_names[entry.entry_name] = metadata.get("visibleName", "Unknown")
                    break

    if args.list:
        print("\nDocuments:")
        for uuid, name in sorted(doc_names.items(), key=lambda x: x[1]):
            print(f"  {name}: {uuid}")
        return

    # Find matching document
    matches = [(uuid, name) for uuid, name in doc_names.items()
               if args.name.lower() in name.lower()]

    if not matches:
        print(f"No documents matching '{args.name}'")
        print("\nAvailable documents:")
        for uuid, name in sorted(doc_names.items(), key=lambda x: x[1]):
            print(f"  {name}")
        sys.exit(1)

    if len(matches) > 1:
        print(f"Multiple matches for '{args.name}':")
        for uuid, name in matches:
            print(f"  {name}: {uuid}")
        print("\nBe more specific.")
        sys.exit(1)

    doc_uuid, doc_name = matches[0]
    print(f"Found: '{doc_name}' ({doc_uuid})")

    # Download the document's .rm files
    for entry in root_docs:
        if entry.entry_name == doc_uuid:
            doc_index = sync.download_blob(entry.hash)
            doc_files = sync.parse_index(doc_index)

            output_dir = Path(args.output)
            output_dir.mkdir(parents=True, exist_ok=True)

            for file_entry in doc_files:
                if file_entry.entry_name.endswith(".rm"):
                    content = sync.download_blob(file_entry.hash)
                    # Use just the page UUID as filename
                    page_uuid = file_entry.entry_name.split("/")[-1]
                    output_path = output_dir / page_uuid
                    output_path.write_bytes(content)
                    print(f"Downloaded: {output_path} ({len(content)} bytes)")

            print(f"\nDone! Files saved to {output_dir}")
            return

    print("Error: Document entry not found")
    sys.exit(1)


if __name__ == "__main__":
    main()
