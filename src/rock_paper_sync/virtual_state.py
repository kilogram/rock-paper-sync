"""Virtual device state for atomic cloud sync operations.

This module implements the VirtualDeviceState pattern - a transient mirror of the
intended cloud state that allows all operations (uploads, deletions, merges) to be
staged, then applied in a single atomic root update.

Analogous to React's Virtual DOM:
- React: Virtual DOM → compute changes → single DOM update
- Cloud Sync: VirtualDeviceState → compute changes → single root update

This ensures:
1. Single generation increment per multi-step operation (not 1 per step)
2. True all-or-nothing atomicity at cloud level
3. Zero partial failure states
4. Safe, idempotent retry semantics
"""

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Constants from sync_v3.py
SCHEMA_VERSION = "3"
DOC_TYPE = "80000000"
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


class VirtualDeviceState:
    """Transient representation of intended cloud state.

    Represents the files/folders we want on the device after sync completes.
    All operations (uploads, deletes, merges) modify this state.
    Once all operations staged, compute final root hash and apply atomically.

    Attributes:
        entries: Dict mapping UUID → BlobEntry for O(1) operations
        original_hash: Root hash from cloud before any operations
        original_gen: Generation number from cloud before any operations
    """

    def __init__(self, current_entries: list[BlobEntry], current_hash: str, current_gen: int):
        """Initialize from current cloud state.

        Args:
            current_entries: Current document entries in cloud root
            current_hash: Current root hash from cloud
            current_gen: Current generation number
        """
        # Map UUID → BlobEntry for efficient operations
        self.entries = {entry.entry_name: entry for entry in current_entries}

        # Track original state for conflict detection and change detection
        self.original_hash = current_hash
        self.original_gen = current_gen

    def add_or_update_document(
        self,
        doc_uuid: str,
        hash_of_hashes: str,
        num_files: int
    ) -> None:
        """Stage a document upload or update.

        Creates or updates a document entry in the virtual state. The entry
        represents a document that has been uploaded to blob storage and is
        ready to be merged into the root index.

        Args:
            doc_uuid: Document UUID
            hash_of_hashes: Hash of all files in document
            num_files: Number of files in document
        """
        self.entries[doc_uuid] = BlobEntry(
            hash=hash_of_hashes,
            type=DOC_TYPE,
            entry_name=doc_uuid,
            subfiles=num_files,
            size=0,
        )
        logger.debug(f"Virtual state: added/updated document {doc_uuid[:8]}...")

    def delete_document(self, doc_uuid: str) -> bool:
        """Stage a document deletion.

        Removes a document entry from the virtual state, representing the
        deletion of that document from the cloud.

        Args:
            doc_uuid: Document UUID to delete

        Returns:
            True if document was in entries and removed, False if not found
        """
        if doc_uuid in self.entries:
            del self.entries[doc_uuid]
            logger.debug(f"Virtual state: deleted document {doc_uuid[:8]}...")
            return True
        return False

    def compute_final_hash(self) -> str:
        """Compute root hash from staged entries.

        Computes the root hash by:
        1. Sorting entries by entry_name
        2. Converting each entry to index line format
        3. Computing SHA256 of the resulting index content

        This is a pure function - same entries always produce same hash.

        Returns:
            Root hash if entries would change, None if unchanged
        """
        # Build index content (same as sync_v3.upload_index)
        lines = [SCHEMA_VERSION]
        for entry in sorted(self.entries.values(), key=lambda e: e.entry_name):
            lines.append(entry.to_line())

        index_content = "\n".join(lines).encode("utf-8")
        index_hash = hashlib.sha256(index_content).hexdigest()

        return index_hash

    def has_changes(self) -> bool:
        """Check if virtual state differs from original cloud state.

        Compares the hash of the virtual state against the original cloud
        hash to determine if any changes have been staged.

        Returns:
            True if virtual state has changes from original, False if unchanged
        """
        return self.compute_final_hash() != self.original_hash

    def get_entries(self) -> list[BlobEntry]:
        """Get all entries in virtual state.

        Returns:
            List of all document entries (unsorted)
        """
        return list(self.entries.values())

    def get_entry_count(self) -> int:
        """Get number of documents in virtual state.

        Returns:
            Number of document entries
        """
        return len(self.entries)
