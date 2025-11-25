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


class GenerationConflictError(Exception):
    """Raised when root generation conflict is detected (optimistic concurrency control)."""

    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Generation conflict: expected {expected}, server has {actual}"
        )

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

    def get_document_page_uuids(self, doc_uuid: str) -> list[str]:
        """
        Get list of page UUIDs for an existing document.

        Args:
            doc_uuid: Document UUID

        Returns:
            List of page UUIDs in order. Empty list if document doesn't exist.

        Raises:
            requests.HTTPError: If request fails
        """
        import json

        # Find document in root
        root_docs = self.get_root_documents()
        doc_entry = None
        for entry in root_docs:
            if entry.entry_name == doc_uuid:
                doc_entry = entry
                break

        if not doc_entry:
            logger.debug(f"Document {doc_uuid} not found in root")
            return []

        # Download document index
        doc_index_content = self.download_blob(doc_entry.hash)
        doc_files = self.parse_index(doc_index_content)

        # Find .content file
        content_entry = None
        for entry in doc_files:
            if entry.entry_name.endswith('.content'):
                content_entry = entry
                break

        if not content_entry:
            logger.warning(f"No .content file found for document {doc_uuid}")
            return []

        # Download and parse .content file
        content_data = self.download_blob(content_entry.hash)
        content_json = json.loads(content_data)

        # Extract page UUIDs from cPages structure (formatVersion 2)
        if 'cPages' in content_json and 'pages' in content_json['cPages']:
            # Sort by idx value to maintain order
            pages = content_json['cPages']['pages']
            sorted_pages = sorted(pages, key=lambda p: p.get('idx', {}).get('value', ''))
            return [page['id'] for page in sorted_pages]

        # Fallback for formatVersion 1 (pages array)
        elif 'pages' in content_json:
            pages = content_json['pages']
            if isinstance(pages, list) and len(pages) > 0 and isinstance(pages[0], str):
                return pages

        logger.warning(f"Could not extract page UUIDs from .content for {doc_uuid}")
        return []

    def update_root(
        self, root_hash: str, generation: int, broadcast: bool = True
    ) -> int:
        """
        Update the root hash tree with optimistic concurrency control.

        Args:
            root_hash: Hash of the root index
            generation: Current generation number (will be incremented)
            broadcast: Whether to broadcast sync notification (triggers xochitl reload)

        Returns:
            New generation number

        Raises:
            GenerationConflictError: If another client updated root concurrently
            requests.HTTPError: If update fails for other reasons
        """
        url = f"{self.base_url}/sync/v3/root"

        payload = {
            "generation": generation,
            "hash": root_hash,
            "broadcast": broadcast,
        }

        logger.info(f"Updating root to {root_hash} (gen {generation}, broadcast={broadcast})")
        response = requests.put(url, headers=self.headers, json=payload)

        # Check for generation conflict (optimistic concurrency control)
        if response.status_code == 409:
            # Conflict - someone else updated the root
            current_hash, current_gen = self.get_current_generation()
            logger.warning(
                f"Generation conflict: expected {generation}, server has {current_gen}"
            )
            raise GenerationConflictError(expected=generation, actual=current_gen)

        response.raise_for_status()

        result = response.json()
        new_generation = result.get("generation", generation + 1)
        logger.info(f"Root updated successfully (new gen: {new_generation})")
        return new_generation

    def _retry_root_update(
        self,
        operation_name: str,
        modify_root_fn,
        broadcast: bool = True,
        max_retries: int = 3,
    ) -> int:
        """
        Execute a root modification operation with optimistic concurrency retry.

        This helper implements the read-modify-write pattern with automatic retry
        on generation conflicts, eliminating duplication across root operations.

        Args:
            operation_name: Name for logging (e.g., "merge document", "delete documents")
            modify_root_fn: Function that takes current root entries and returns modified entries.
                           Should be idempotent as it may be called multiple times on retry.
            broadcast: Whether to trigger sync notification to device
            max_retries: Maximum retry attempts on conflicts

        Returns:
            New generation number after successful update

        Raises:
            GenerationConflictError: If max retries exceeded
            requests.HTTPError: If update fails for other reasons
        """
        for attempt in range(max_retries):
            try:
                # Read current state
                current_root_hash, current_generation = self.get_current_generation()
                root_entries = self.get_root_documents()

                logger.debug(
                    f"Attempt {attempt + 1}/{max_retries} for {operation_name} "
                    f"(current gen: {current_generation})"
                )

                # Modify (operation-specific)
                new_entries = modify_root_fn(root_entries)

                # Write new state
                root_index_hash, _ = self.upload_index(new_entries)
                logger.debug(f"  New root index hash: {root_index_hash}")

                new_generation = self.update_root(
                    root_index_hash, current_generation, broadcast
                )

                logger.info(
                    f"Successfully completed {operation_name} (gen {new_generation})"
                )
                return new_generation

            except GenerationConflictError as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Generation conflict during {operation_name} on attempt "
                        f"{attempt + 1}/{max_retries} (expected {e.expected}, "
                        f"got {e.actual}), retrying..."
                    )
                    continue
                else:
                    logger.error(
                        f"Max retries ({max_retries}) exceeded for {operation_name}"
                    )
                    raise

        # Unreachable but makes type checker happy
        raise RuntimeError("Unreachable: retry loop exited without return or raise")

    def upload_document_files(
        self, doc_uuid: str, files: dict[str, bytes]
    ) -> tuple[str, list[BlobEntry]]:
        """
        Upload document files as blobs and return hash-of-hashes (protocol-only operation).

        This is a low-level method that only handles blob uploads without touching
        the root index. Use this when you want to manage root updates separately.

        Args:
            doc_uuid: Document UUID
            files: Dict mapping filename -> content bytes

        Returns:
            Tuple of (hash_of_hashes, file_entries)
                - hash_of_hashes: The hashOfHashesV3 for this document
                - file_entries: List of BlobEntry objects for the uploaded files

        Raises:
            requests.HTTPError: If upload fails
        """
        logger.info(f"Uploading document files for {doc_uuid}")

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
        doc_index_hash, doc_index_content = self.upload_index(file_entries)
        logger.debug(f"  Document index hash: {doc_index_hash}")

        # Step 3: Calculate hashOfHashesV3 for the document
        # The device expects the hash in the root to be SHA256 of concatenated binary file hashes
        file_hashes_binary = b"".join(
            bytes.fromhex(entry.hash)
            for entry in sorted(file_entries, key=lambda e: e.entry_name)
        )
        hash_of_hashes = self._sha256(file_hashes_binary)
        logger.debug(f"  Hash-of-hashes (hashOfHashesV3): {hash_of_hashes}")

        # Step 4: Upload the document index AGAIN under the hashOfHashesV3
        # The device will try to download the index using this hash
        if hash_of_hashes != doc_index_hash:
            self.upload_blob(hash_of_hashes, doc_index_content)
            logger.debug(f"  Uploaded document index under hashOfHashesV3")

        return hash_of_hashes, file_entries

    def merge_document_into_root(
        self, doc_uuid: str, hash_of_hashes: str, num_files: int, broadcast: bool = True, max_retries: int = 3
    ) -> int:
        """
        Merge a document into the root index with retry logic.

        Args:
            doc_uuid: Document UUID
            hash_of_hashes: The hashOfHashesV3 for this document
            num_files: Number of files in the document
            broadcast: Whether to trigger sync notification
            max_retries: Maximum number of retry attempts on conflicts

        Returns:
            New generation number

        Raises:
            requests.HTTPError: If update fails
            GenerationConflictError: If max retries exceeded
        """
        doc_entry = BlobEntry(
            hash=hash_of_hashes,
            type=DOC_TYPE,
            entry_name=doc_uuid,
            subfiles=num_files,
            size=0,
        )

        def _merge_operation(entries: list[BlobEntry]) -> list[BlobEntry]:
            """Update or add document to root entries."""
            # Look for existing document
            for i, entry in enumerate(entries):
                if entry.entry_name == doc_uuid:
                    entries[i] = doc_entry
                    logger.debug(f"  Updating existing document {doc_uuid}")
                    return entries

            # Not found, add new
            entries.append(doc_entry)
            logger.debug(f"  Adding new document {doc_uuid}")
            return entries

        return self._retry_root_update(
            operation_name=f"merge document {doc_uuid}",
            modify_root_fn=_merge_operation,
            broadcast=broadcast,
            max_retries=max_retries,
        )

    def upload_document(
        self, doc_uuid: str, files: dict[str, bytes], broadcast: bool = True
    ) -> None:
        """
        Upload a complete document using Sync v3 protocol (high-level convenience method).

        This combines file upload and root merging with automatic retry on conflicts.

        Args:
            doc_uuid: Document UUID
            files: Dict mapping filename -> content bytes
                   e.g., {"{uuid}.metadata": b"...", "{uuid}.content": b"...", ...}
            broadcast: Whether to trigger sync notification to xochitl

        Raises:
            requests.HTTPError: If upload fails
            GenerationConflictError: If max retries exceeded
        """
        logger.info(f"Uploading document {doc_uuid} via Sync v3")

        # Upload files and get hash-of-hashes
        hash_of_hashes, file_entries = self.upload_document_files(doc_uuid, files)

        # Merge into root with retry logic
        self.merge_document_into_root(
            doc_uuid, hash_of_hashes, len(file_entries), broadcast
        )

    def delete_document(
        self, doc_uuid: str, broadcast: bool = True, max_retries: int = 3
    ) -> None:
        """
        Delete a document using Sync v3 protocol with retry logic.

        This is a convenience wrapper around delete_documents_batch for single documents.

        Args:
            doc_uuid: Document UUID to delete
            broadcast: Whether to trigger sync notification to xochitl
            max_retries: Maximum number of retry attempts on conflicts

        Raises:
            requests.HTTPError: If delete fails
            GenerationConflictError: If max retries exceeded
        """
        self.delete_documents_batch([doc_uuid], broadcast, max_retries)

    def delete_documents_batch(
        self, doc_uuids: list[str], broadcast: bool = True, max_retries: int = 3
    ) -> None:
        """
        Delete multiple documents in a single root update.

        This is more efficient than calling delete_document() multiple times and
        avoids device sync issues when deleting related documents (like nested folders).
        The device only receives one notification showing the final state.

        Args:
            doc_uuids: List of document UUIDs to delete
            broadcast: Whether to trigger sync notification to xochitl
            max_retries: Maximum number of retry attempts on conflicts

        Raises:
            requests.HTTPError: If delete fails
            GenerationConflictError: If max retries exceeded
        """
        if not doc_uuids:
            logger.warning("delete_documents_batch called with empty list")
            return

        logger.info(f"Deleting {len(doc_uuids)} documents in batch via Sync v3")
        logger.debug(f"  UUIDs: {doc_uuids}")

        uuids_to_delete = set(doc_uuids)

        def _delete_operation(entries: list[BlobEntry]) -> list[BlobEntry]:
            """Remove documents from root entries."""
            new_entries = []
            deleted_count = 0

            for entry in entries:
                if entry.entry_name in uuids_to_delete:
                    deleted_count += 1
                    logger.debug(
                        f"  Removing document: {entry.entry_name} ({entry.subfiles} files)"
                    )
                else:
                    new_entries.append(entry)

            if deleted_count == 0:
                logger.warning(
                    f"None of the {len(doc_uuids)} documents found in root "
                    f"(root has {len(entries)} entries)"
                )
            elif deleted_count < len(doc_uuids):
                logger.warning(
                    f"Only {deleted_count}/{len(doc_uuids)} documents found in root"
                )
            else:
                logger.debug(
                    f"  Successfully removed {deleted_count} documents "
                    f"({len(new_entries)} remaining)"
                )

            return new_entries

        self._retry_root_update(
            operation_name=f"delete {len(doc_uuids)} documents",
            modify_root_fn=_delete_operation,
            broadcast=broadcast,
            max_retries=max_retries,
        )
