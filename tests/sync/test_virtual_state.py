"""Unit tests for VirtualDeviceState class.

Tests verify that VirtualDeviceState correctly:
1. Tracks the intended cloud state
2. Computes root hashes deterministically
3. Detects changes from original state
4. Allows atomic multi-step operations
"""

import hashlib

from rock_paper_sync.virtual_state import (
    DELIMITER,
    DOC_TYPE,
    SCHEMA_VERSION,
    BlobEntry,
    VirtualDeviceState,
)


class TestBlobEntry:
    """Test BlobEntry formatting."""

    def test_to_line_document_entry(self) -> None:
        """Test BlobEntry.to_line() formats correctly for documents."""
        entry = BlobEntry(
            hash="abc123def456",
            type=DOC_TYPE,
            entry_name="doc-uuid-123",
            subfiles=5,
            size=0,
        )
        line = entry.to_line()

        expected = (
            f"abc123def456{DELIMITER}{DOC_TYPE}{DELIMITER}doc-uuid-123{DELIMITER}5{DELIMITER}0"
        )
        assert line == expected

    def test_to_line_file_entry(self) -> None:
        """Test BlobEntry.to_line() formats correctly for files."""
        entry = BlobEntry(
            hash="file_hash_123",
            type="0",
            entry_name="file_123",
            subfiles=0,
            size=1024,
        )
        line = entry.to_line()

        expected = f"file_hash_123{DELIMITER}0{DELIMITER}file_123{DELIMITER}0{DELIMITER}1024"
        assert line == expected


class TestVirtualDeviceStateInit:
    """Test VirtualDeviceState initialization."""

    def test_init_empty_state(self) -> None:
        """Test initialization with no entries."""
        vds = VirtualDeviceState([], "hash123", 0)

        assert vds.get_entry_count() == 0
        assert vds.original_hash == "hash123"
        assert vds.original_gen == 0

    def test_init_with_entries(self) -> None:
        """Test initialization with existing entries."""
        entries = [
            BlobEntry("hash1", DOC_TYPE, "uuid1", 3, 0),
            BlobEntry("hash2", DOC_TYPE, "uuid2", 5, 0),
        ]
        vds = VirtualDeviceState(entries, "original_hash", 5)

        assert vds.get_entry_count() == 2
        assert vds.original_hash == "original_hash"
        assert vds.original_gen == 5
        assert "uuid1" in vds.entries
        assert "uuid2" in vds.entries


class TestVirtualDeviceStateOperations:
    """Test VirtualDeviceState operations (add, delete)."""

    def test_add_or_update_new_document(self) -> None:
        """Test adding a new document."""
        vds = VirtualDeviceState([], "hash", 0)

        vds.add_or_update_document("new-doc-uuid", "new_hash", 3)

        assert vds.get_entry_count() == 1
        assert "new-doc-uuid" in vds.entries
        entry = vds.entries["new-doc-uuid"]
        assert entry.hash == "new_hash"
        assert entry.type == DOC_TYPE
        assert entry.subfiles == 3

    def test_add_or_update_existing_document(self) -> None:
        """Test updating an existing document."""
        entries = [BlobEntry("old_hash", DOC_TYPE, "doc-uuid", 2, 0)]
        vds = VirtualDeviceState(entries, "hash", 0)

        vds.add_or_update_document("doc-uuid", "new_hash", 5)

        assert vds.get_entry_count() == 1
        entry = vds.entries["doc-uuid"]
        assert entry.hash == "new_hash"
        assert entry.subfiles == 5

    def test_delete_existing_document(self) -> None:
        """Test deleting an existing document."""
        entries = [
            BlobEntry("hash1", DOC_TYPE, "uuid1", 3, 0),
            BlobEntry("hash2", DOC_TYPE, "uuid2", 5, 0),
        ]
        vds = VirtualDeviceState(entries, "hash", 0)

        result = vds.delete_document("uuid1")

        assert result is True
        assert vds.get_entry_count() == 1
        assert "uuid1" not in vds.entries
        assert "uuid2" in vds.entries

    def test_delete_nonexistent_document(self) -> None:
        """Test deleting a non-existent document."""
        vds = VirtualDeviceState([], "hash", 0)

        result = vds.delete_document("nonexistent")

        assert result is False
        assert vds.get_entry_count() == 0


class TestComputeFinalHash:
    """Test root hash computation."""

    def test_hash_empty_state(self) -> None:
        """Test hash of empty state."""
        vds = VirtualDeviceState([], None, 0)

        hash_result = vds.compute_final_hash()

        # Should be hash of just schema version (no trailing newline from join())
        expected = hashlib.sha256(SCHEMA_VERSION.encode()).hexdigest()
        assert hash_result == expected

    def test_hash_deterministic(self) -> None:
        """Test that same state always produces same hash."""
        entries = [
            BlobEntry("hash1", DOC_TYPE, "uuid1", 3, 0),
            BlobEntry("hash2", DOC_TYPE, "uuid2", 5, 0),
        ]
        vds1 = VirtualDeviceState(entries.copy(), "original", 0)
        vds2 = VirtualDeviceState(entries.copy(), "original", 0)

        assert vds1.compute_final_hash() == vds2.compute_final_hash()

    def test_hash_sorted_by_entry_name(self) -> None:
        """Test that entries are sorted by name in hash computation."""
        # Create entries in reverse order
        entries_reverse = [
            BlobEntry("hash_z", DOC_TYPE, "uuid_z", 3, 0),
            BlobEntry("hash_a", DOC_TYPE, "uuid_a", 5, 0),
        ]
        # Create entries in forward order
        entries_forward = [
            BlobEntry("hash_a", DOC_TYPE, "uuid_a", 5, 0),
            BlobEntry("hash_z", DOC_TYPE, "uuid_z", 3, 0),
        ]

        vds_reverse = VirtualDeviceState(entries_reverse, "original", 0)
        vds_forward = VirtualDeviceState(entries_forward, "original", 0)

        # Both should produce same hash despite different order
        assert vds_reverse.compute_final_hash() == vds_forward.compute_final_hash()

    def test_hash_changes_with_different_entries(self) -> None:
        """Test that different entries produce different hashes."""
        entries1 = [BlobEntry("hash1", DOC_TYPE, "uuid1", 3, 0)]
        entries2 = [BlobEntry("hash2", DOC_TYPE, "uuid2", 5, 0)]

        vds1 = VirtualDeviceState(entries1, "original", 0)
        vds2 = VirtualDeviceState(entries2, "original", 0)

        assert vds1.compute_final_hash() != vds2.compute_final_hash()


class TestChangeDetection:
    """Test change detection via has_changes()."""

    def test_no_changes_when_same_entries(self) -> None:
        """Test has_changes returns False when entries unchanged."""
        # Compute the hash of a state with one entry
        lines = [
            SCHEMA_VERSION,
            f"abc123{DELIMITER}{DOC_TYPE}{DELIMITER}uuid1{DELIMITER}3{DELIMITER}0",
        ]
        original_hash = hashlib.sha256("\n".join(lines).encode()).hexdigest()
        entries = [BlobEntry("abc123", DOC_TYPE, "uuid1", 3, 0)]

        vds = VirtualDeviceState(entries, original_hash, 0)

        assert vds.has_changes() is False

    def test_changes_detected_after_delete(self) -> None:
        """Test has_changes returns True after deletion."""
        entries = [BlobEntry("hash1", DOC_TYPE, "uuid1", 3, 0)]
        original_hash = hashlib.sha256(
            f"{SCHEMA_VERSION}\nhash1{DELIMITER}80000000{DELIMITER}uuid1{DELIMITER}3{DELIMITER}0\n".encode()
        ).hexdigest()

        vds = VirtualDeviceState(entries.copy(), original_hash, 0)
        vds.delete_document("uuid1")

        assert vds.has_changes() is True

    def test_changes_detected_after_add(self) -> None:
        """Test has_changes returns True after addition."""
        original_hash = hashlib.sha256(f"{SCHEMA_VERSION}\n".encode()).hexdigest()

        vds = VirtualDeviceState([], original_hash, 0)
        vds.add_or_update_document("new-uuid", "new-hash", 2)

        assert vds.has_changes() is True

    def test_no_changes_after_add_and_delete(self) -> None:
        """Test has_changes returns False after add and delete cancel out."""
        entries = [BlobEntry("hash1", DOC_TYPE, "uuid1", 3, 0)]
        # Original hash includes entry1
        lines = [
            SCHEMA_VERSION,
            f"hash1{DELIMITER}{DOC_TYPE}{DELIMITER}uuid1{DELIMITER}3{DELIMITER}0",
        ]
        original_hash = hashlib.sha256("\n".join(lines).encode()).hexdigest()

        vds = VirtualDeviceState(entries.copy(), original_hash, 0)
        vds.delete_document("uuid1")
        vds.add_or_update_document("uuid1", "hash1", 3)

        assert vds.has_changes() is False


class TestAtomicOperations:
    """Test multi-step atomic operations."""

    def test_atomic_deletion_multiple_documents(self) -> None:
        """Test atomic deletion of multiple documents in one operation."""
        entries = [
            BlobEntry("h1", DOC_TYPE, "doc1", 2, 0),
            BlobEntry("h2", DOC_TYPE, "doc2", 3, 0),
            BlobEntry("h3", DOC_TYPE, "doc3", 4, 0),
        ]
        # Original hash with all 3 entries
        lines = [
            SCHEMA_VERSION,
            f"h1{DELIMITER}{DOC_TYPE}{DELIMITER}doc1{DELIMITER}2{DELIMITER}0",
            f"h2{DELIMITER}{DOC_TYPE}{DELIMITER}doc2{DELIMITER}3{DELIMITER}0",
            f"h3{DELIMITER}{DOC_TYPE}{DELIMITER}doc3{DELIMITER}4{DELIMITER}0",
        ]
        original_hash = hashlib.sha256("\n".join(lines).encode()).hexdigest()

        vds = VirtualDeviceState(entries, original_hash, 5)

        # Stage all deletions
        vds.delete_document("doc1")
        vds.delete_document("doc2")
        # Note: doc3 still present

        # Verify single atomic hash
        final_hash = vds.compute_final_hash()

        # Should only contain doc3
        lines_final = [
            SCHEMA_VERSION,
            f"h3{DELIMITER}{DOC_TYPE}{DELIMITER}doc3{DELIMITER}4{DELIMITER}0",
        ]
        expected = hashlib.sha256("\n".join(lines_final).encode()).hexdigest()
        assert final_hash == expected

        # Verify changes are detected
        assert vds.has_changes() is True

    def test_atomic_upload_and_delete(self) -> None:
        """Test atomic combination of uploads and deletions."""
        entries = [
            BlobEntry("h1", DOC_TYPE, "old1", 2, 0),
            BlobEntry("h2", DOC_TYPE, "old2", 3, 0),
        ]
        original_hash = hashlib.sha256(
            f"{SCHEMA_VERSION}\n"
            f"h1{DELIMITER}80000000{DELIMITER}old1{DELIMITER}2{DELIMITER}0\n"
            f"h2{DELIMITER}80000000{DELIMITER}old2{DELIMITER}3{DELIMITER}0\n".encode()
        ).hexdigest()

        vds = VirtualDeviceState(entries, original_hash, 3)

        # Stage operations
        vds.delete_document("old1")
        vds.add_or_update_document("new1", "h_new1", 4)

        # Verify final state
        assert vds.get_entry_count() == 2  # old2 + new1
        assert "old1" not in vds.entries
        assert "old2" in vds.entries
        assert "new1" in vds.entries
        assert vds.has_changes() is True


class TestGetters:
    """Test getter methods."""

    def test_get_entries(self) -> None:
        """Test get_entries returns all entries."""
        entries = [
            BlobEntry("h1", DOC_TYPE, "uuid1", 2, 0),
            BlobEntry("h2", DOC_TYPE, "uuid2", 3, 0),
        ]
        vds = VirtualDeviceState(entries, "hash", 0)

        result = vds.get_entries()

        assert len(result) == 2
        uuids = {e.entry_name for e in result}
        assert uuids == {"uuid1", "uuid2"}

    def test_get_entry_count(self) -> None:
        """Test get_entry_count returns correct count."""
        entries = [
            BlobEntry("h1", DOC_TYPE, "uuid1", 2, 0),
            BlobEntry("h2", DOC_TYPE, "uuid2", 3, 0),
        ]
        vds = VirtualDeviceState(entries, "hash", 0)

        assert vds.get_entry_count() == 2

        vds.delete_document("uuid1")

        assert vds.get_entry_count() == 1
