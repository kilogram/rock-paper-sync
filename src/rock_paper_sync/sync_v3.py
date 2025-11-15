"""
Sync v3 protocol client for rm_cloud.

Implements the hash-based blob storage protocol used by reMarkable devices.
"""

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "3"
DOC_TYPE = "80000000"
FILE_TYPE = "0"
DELIMITER = ":"


@dataclass
class BlobEntry:
    """Entry in an index file."""

    hash: str
    type: str  # "80000000" for docs, "0" for files
    entry_name: str  # UUID or filename
    subfiles: int  # Number of files (for docs) or 0
    size: int  # File size in bytes

    def to_line(self) -> str:
        """Format as index line."""
        return f"{self.hash}{DELIMITER}{self.type}{DELIMITER}{self.entry_name}{DELIMITER}{self.subfiles}{DELIMITER}{self.size}"


class SyncV3Client:
    """
    Client for rm_cloud Sync v3 protocol.

    Uploads documents using hash-based blob storage.
    """

    def __init__(self, base_url: str, device_token: str):
        """
        Initialize Sync v3 client.

        Args:
            base_url: Base URL of rm_cloud
            device_token: JWT device token for authentication
        """
        self.base_url = base_url.rstrip("/")
        self.device_token = device_token
        self.headers = {"Authorization": f"Bearer {device_token}"}

    def _sha256(self, data: bytes) -> str:
        """Calculate SHA256 hash of data."""
        return hashlib.sha256(data).hexdigest()

    def upload_blob(self, blob_hash: str, content: bytes) -> None:
        """
        Upload a blob to storage.

        Args:
            blob_hash: SHA256 hash of the content
            content: File content bytes

        Raises:
            requests.HTTPError: If upload fails
        """
        url = f"{self.base_url}/sync/v3/files/{blob_hash}"

        logger.debug(f"Uploading blob {blob_hash} ({len(content)} bytes)")
        response = requests.put(url, headers=self.headers, data=content)
        response.raise_for_status()

    def upload_file_as_blob(self, file_path: Path) -> tuple[str, int]:
        """
        Upload a file as a blob and return its hash and size.

        Args:
            file_path: Path to file to upload

        Returns:
            Tuple of (hash, size)

        Raises:
            requests.HTTPError: If upload fails
        """
        content = file_path.read_bytes()
        file_hash = self._sha256(content)
        self.upload_blob(file_hash, content)
        return file_hash, len(content)

    def upload_index(self, entries: list[BlobEntry]) -> tuple[str, bytes]:
        """
        Create an index file, upload it as a blob, return hash and content.

        Args:
            entries: List of entries to include in index

        Returns:
            Tuple of (index_hash, index_content)
        """
        # Build index content
        lines = [SCHEMA_VERSION]
        for entry in sorted(entries, key=lambda e: e.entry_name):
            lines.append(entry.to_line())

        index_content = "\n".join(lines).encode("utf-8")
        index_hash = self._sha256(index_content)

        # Upload index as a blob
        self.upload_blob(index_hash, index_content)

        return index_hash, index_content

    def get_current_generation(self) -> tuple[Optional[str], int]:
        """
        Get current root hash and generation from server.

        Returns:
            Tuple of (root_hash, generation). Returns (None, 0) if no root exists.

        Raises:
            requests.HTTPError: If request fails
        """
        url = f"{self.base_url}/sync/v3/root"

        response = requests.get(url, headers=self.headers)
        if response.status_code == 404:
            logger.info("No root exists yet (new account)")
            return None, 0

        response.raise_for_status()
        data = response.json()
        return data.get("hash"), data.get("generation", 0)

    def download_blob(self, blob_hash: str) -> bytes:
        """
        Download a blob from storage.

        Args:
            blob_hash: Hash of the blob to download

        Returns:
            Blob content as bytes

        Raises:
            requests.HTTPError: If download fails
        """
        url = f"{self.base_url}/sync/v3/files/{blob_hash}"

        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.content

    def parse_index(self, index_content: bytes) -> list[BlobEntry]:
        """
        Parse an index file into entries.

        Args:
            index_content: Raw index file content

        Returns:
            List of BlobEntry objects
        """
        lines = index_content.decode("utf-8").strip().split("\n")

        # First line is schema version
        schema = lines[0] if lines else ""
        if schema != SCHEMA_VERSION:
            logger.warning(f"Unknown schema version: {schema}")

        entries = []
        for line in lines[1:]:
            if not line:
                continue

            parts = line.split(DELIMITER)
            if len(parts) != 5:
                logger.warning(f"Invalid index line: {line}")
                continue

            entries.append(
                BlobEntry(
                    hash=parts[0],
                    type=parts[1],
                    entry_name=parts[2],
                    subfiles=int(parts[3]),
                    size=int(parts[4]),
                )
            )

        return entries

    def get_root_documents(self) -> list[BlobEntry]:
        """
        Get list of all documents in current root.

        Returns:
            List of document entries. Empty list if no root exists.

        Raises:
            requests.HTTPError: If request fails
        """
        root_hash, _ = self.get_current_generation()
        if not root_hash:
            return []

        # Download and parse root index
        root_content = self.download_blob(root_hash)
        return self.parse_index(root_content)

    def update_root(
        self, root_hash: str, generation: int, broadcast: bool = True
    ) -> int:
        """
        Update the root hash tree.

        Args:
            root_hash: Hash of the root index
            generation: Current generation number (will be incremented)
            broadcast: Whether to broadcast sync notification (triggers xochitl reload)

        Returns:
            New generation number

        Raises:
            requests.HTTPError: If update fails
        """
        url = f"{self.base_url}/sync/v3/root"

        payload = {
            "generation": generation,
            "hash": root_hash,
            "broadcast": broadcast,
        }

        logger.info(f"Updating root to {root_hash} (gen {generation})")
        response = requests.put(url, headers=self.headers, json=payload)
        response.raise_for_status()

        result = response.json()
        new_generation = result.get("generation", generation + 1)
        logger.info(f"Root updated successfully (new gen: {new_generation})")
        return new_generation

    def upload_document(
        self,
        doc_uuid: str,
        files: dict[str, bytes],
        broadcast: bool = True,
    ) -> None:
        """
        Upload a complete document using Sync v3 protocol.

        Args:
            doc_uuid: Document UUID
            files: Dict mapping filename -> content bytes
                   e.g., {"{uuid}.metadata": b"...", "{uuid}.content": b"...", ...}
            broadcast: Whether to trigger sync notification to xochitl

        Raises:
            requests.HTTPError: If upload fails
        """
        logger.info(f"Uploading document {doc_uuid} via Sync v3")

        # Step 1: Upload all individual files as blobs
        file_entries = []
        for filename, content in files.items():
            file_hash = self._sha256(content)
            self.upload_blob(file_hash, content)
            file_entries.append(
                BlobEntry(
                    hash=file_hash,
                    type=FILE_TYPE,
                    entry_name=filename,
                    subfiles=0,
                    size=len(content),
                )
            )
            logger.debug(f"  Uploaded {filename} ({len(content)} bytes)")

        # Step 2: Create document index and upload it
        doc_index_hash, _ = self.upload_index(file_entries)
        logger.debug(f"  Document index hash: {doc_index_hash}")

        # Step 3: Get current root documents and merge our document
        current_root_hash, current_generation = self.get_current_generation()
        root_entries = self.get_root_documents()

        # Step 4: Update or add our document to the root
        doc_entry = BlobEntry(
            hash=doc_index_hash,
            type=DOC_TYPE,
            entry_name=doc_uuid,
            subfiles=len(file_entries),
            size=0,
        )

        # Find and replace if document already exists, otherwise append
        found = False
        for i, entry in enumerate(root_entries):
            if entry.entry_name == doc_uuid:
                root_entries[i] = doc_entry
                found = True
                logger.debug(f"  Updating existing document in root")
                break

        if not found:
            root_entries.append(doc_entry)
            logger.debug(f"  Adding new document to root")

        # Step 5: Create new root index and upload it
        root_index_hash, _ = self.upload_index(root_entries)
        logger.debug(f"  Root index hash: {root_index_hash}")

        # Step 6: Update root (triggers broadcast if enabled)
        new_generation = self.update_root(
            root_index_hash, current_generation, broadcast
        )

        logger.info(
            f"Document {doc_uuid} uploaded successfully (gen {new_generation})"
        )
