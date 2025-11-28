"""Tests for snapshot storage system (Phase 2).

Tests the content-addressable snapshot storage for:
- ContentStore: Filesystem storage with deduplication
- SnapshotStore: File and annotation block snapshot management
- StateManager integration: Access via state.snapshots property
"""

import sqlite3

import pytest

from rock_paper_sync.annotations.common.snapshots import ContentStore, SnapshotStore
from rock_paper_sync.state import StateManager


class TestContentStore:
    """Tests for ContentStore (content-addressable filesystem storage)."""

    def test_put_and_get(self, tmp_path):
        """Test storing and retrieving content."""
        store = ContentStore(tmp_path)
        content = b"Hello, world!"

        # Store content
        content_hash = store.put(content)

        # Verify hash format (SHA-256)
        assert len(content_hash) == 64
        assert all(c in "0123456789abcdef" for c in content_hash)

        # Retrieve content
        retrieved = store.get(content_hash)
        assert retrieved == content

    def test_deduplication(self, tmp_path):
        """Test automatic deduplication via content addressing."""
        store = ContentStore(tmp_path)
        content = b"Duplicate content"

        # Store same content twice
        hash1 = store.put(content)
        hash2 = store.put(content)

        # Same content -> same hash
        assert hash1 == hash2

        # Only one file stored
        total_files, _ = store.get_size()
        assert total_files == 1

    def test_directory_structure(self, tmp_path):
        """Test Git-style directory structure."""
        store = ContentStore(tmp_path)
        content = b"test content"

        content_hash = store.put(content)

        # Verify path structure: <base>/<first 2>/<next 2>/<full hash>
        expected_path = tmp_path / content_hash[:2] / content_hash[2:4] / content_hash
        assert expected_path.exists()
        assert expected_path.read_bytes() == content

    def test_exists(self, tmp_path):
        """Test checking if content exists."""
        store = ContentStore(tmp_path)
        content = b"test"

        assert not store.exists("nonexistent_hash")

        content_hash = store.put(content)
        assert store.exists(content_hash)

    def test_delete(self, tmp_path):
        """Test deleting content."""
        store = ContentStore(tmp_path)
        content = b"to be deleted"

        content_hash = store.put(content)
        assert store.exists(content_hash)

        # Delete
        deleted = store.delete(content_hash)
        assert deleted is True
        assert not store.exists(content_hash)

        # Delete again (idempotent)
        deleted = store.delete(content_hash)
        assert deleted is False

    def test_get_size(self, tmp_path):
        """Test storage size calculation."""
        store = ContentStore(tmp_path)

        # Empty store
        files, bytes_used = store.get_size()
        assert files == 0
        assert bytes_used == 0

        # Add content
        content1 = b"content one"
        content2 = b"content two is longer"
        store.put(content1)
        store.put(content2)

        files, bytes_used = store.get_size()
        assert files == 2
        assert bytes_used == len(content1) + len(content2)

    def test_get_nonexistent(self, tmp_path):
        """Test retrieving nonexistent content raises error."""
        store = ContentStore(tmp_path)

        with pytest.raises(FileNotFoundError):
            store.get("nonexistent_hash_1234567890abcdef" * 4)  # 64 chars


class TestSnapshotStore:
    """Tests for SnapshotStore (snapshot management API)."""

    @pytest.fixture
    def snapshot_store(self, tmp_path):
        """Create a snapshot store for testing."""
        db_path = tmp_path / "test.db"
        content_dir = tmp_path / "content"

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        content_store = ContentStore(content_dir)
        store = SnapshotStore(conn, content_store)

        yield store

        conn.close()

    def test_snapshot_file(self, snapshot_store):
        """Test creating file snapshots."""
        content = b"# Document\n\nContent here."

        content_hash = snapshot_store.snapshot_file(
            vault_name="test-vault", file_path="Notes/Doc.md", content=content, sync_time=1000
        )

        assert len(content_hash) == 64

        # Verify metadata stored
        cursor = snapshot_store.db.execute(
            "SELECT * FROM file_snapshots WHERE vault_name = ? AND file_path = ?",
            ("test-vault", "Notes/Doc.md"),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["content_hash"] == content_hash
        assert row["file_size"] == len(content)
        assert row["sync_time"] == 1000

    def test_snapshot_file_multiple_versions(self, snapshot_store):
        """Test tracking multiple versions of a file."""
        content_v1 = b"Version 1"
        content_v2 = b"Version 2"
        content_v3 = b"Version 3"

        # Create three snapshots
        snapshot_store.snapshot_file(
            vault_name="vault", file_path="file.md", content=content_v1, sync_time=1000
        )
        snapshot_store.snapshot_file(
            vault_name="vault", file_path="file.md", content=content_v2, sync_time=2000
        )
        snapshot_store.snapshot_file(
            vault_name="vault", file_path="file.md", content=content_v3, sync_time=3000
        )

        # List versions
        versions = snapshot_store.list_file_versions("vault", "file.md")
        assert len(versions) == 3

        # Sorted newest first
        assert versions[0][0] == 3000
        assert versions[1][0] == 2000
        assert versions[2][0] == 1000

    def test_restore_file_latest(self, snapshot_store):
        """Test restoring latest file version."""
        content_v1 = b"Old version"
        content_v2 = b"Latest version"

        snapshot_store.snapshot_file(
            vault_name="vault", file_path="file.md", content=content_v1, sync_time=1000
        )
        snapshot_store.snapshot_file(
            vault_name="vault", file_path="file.md", content=content_v2, sync_time=2000
        )

        # Restore latest (no sync_time specified)
        restored = snapshot_store.restore_file("vault", "file.md")
        assert restored == content_v2

    def test_restore_file_specific_version(self, snapshot_store):
        """Test restoring specific file version."""
        content_v1 = b"Version 1"
        content_v2 = b"Version 2"

        snapshot_store.snapshot_file(
            vault_name="vault", file_path="file.md", content=content_v1, sync_time=1000
        )
        snapshot_store.snapshot_file(
            vault_name="vault", file_path="file.md", content=content_v2, sync_time=2000
        )

        # Restore specific version
        restored = snapshot_store.restore_file("vault", "file.md", sync_time=1000)
        assert restored == content_v1

    def test_restore_nonexistent_file(self, snapshot_store):
        """Test restoring nonexistent file returns None."""
        restored = snapshot_store.restore_file("vault", "nonexistent.md")
        assert restored is None

    def test_snapshot_block(self, snapshot_store):
        """Test creating annotation block snapshots."""
        block_content = "<!-- Highlight: important --> Text here"

        block_hash = snapshot_store.snapshot_block(
            vault_name="vault",
            file_path="file.md",
            paragraph_index=5,
            block_content=block_content,
            annotation_types=["highlight"],
            sync_time=1000,
        )

        assert len(block_hash) == 64

        # Verify metadata
        cursor = snapshot_store.db.execute(
            "SELECT * FROM annotation_blocks WHERE paragraph_index = ?", (5,)
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["block_hash"] == block_hash
        assert row["annotation_types"] == "highlight"

    def test_get_block_snapshot(self, snapshot_store):
        """Test retrieving annotation block snapshots."""
        block_v1 = "<!-- Highlight: text --> Original"
        block_v2 = "<!-- Highlight: text --> Edited"

        # Create two versions
        snapshot_store.snapshot_block(
            vault_name="vault",
            file_path="file.md",
            paragraph_index=3,
            block_content=block_v1,
            annotation_types=["highlight"],
            sync_time=1000,
        )
        snapshot_store.snapshot_block(
            vault_name="vault",
            file_path="file.md",
            paragraph_index=3,
            block_content=block_v2,
            annotation_types=["highlight"],
            sync_time=2000,
        )

        # Get latest
        latest = snapshot_store.get_block_snapshot("vault", "file.md", 3)
        assert latest == block_v2

        # Get specific version
        old = snapshot_store.get_block_snapshot("vault", "file.md", 3, sync_time=1000)
        assert old == block_v1

    def test_multiple_annotation_types(self, snapshot_store):
        """Test blocks with multiple annotation types."""
        block_content = "<!-- Highlight + Stroke --> Mixed annotations"

        snapshot_store.snapshot_block(
            vault_name="vault",
            file_path="file.md",
            paragraph_index=0,
            block_content=block_content,
            annotation_types=["highlight", "stroke"],
            sync_time=1000,
        )

        cursor = snapshot_store.db.execute(
            "SELECT annotation_types FROM annotation_blocks WHERE paragraph_index = 0"
        )
        row = cursor.fetchone()
        assert row["annotation_types"] == "highlight,stroke"

    def test_get_storage_stats(self, snapshot_store):
        """Test storage statistics."""
        # Empty store
        stats = snapshot_store.get_storage_stats()
        assert stats["file_snapshots"] == 0
        assert stats["block_snapshots"] == 0

        # Add snapshots
        snapshot_store.snapshot_file(vault_name="vault", file_path="file1.md", content=b"Content 1")
        snapshot_store.snapshot_file(vault_name="vault", file_path="file2.md", content=b"Content 2")
        snapshot_store.snapshot_block(
            vault_name="vault",
            file_path="file1.md",
            paragraph_index=0,
            block_content="Block 1",
            annotation_types=["highlight"],
        )

        stats = snapshot_store.get_storage_stats()
        assert stats["file_snapshots"] == 2
        assert stats["block_snapshots"] == 1
        assert stats["unique_content"] == 3  # 2 files + 1 block

    def test_content_deduplication(self, snapshot_store):
        """Test that identical content is deduplicated."""
        content = b"Duplicate content"

        # Snapshot same content in different files
        hash1 = snapshot_store.snapshot_file(
            vault_name="vault", file_path="file1.md", content=content, sync_time=1000
        )
        hash2 = snapshot_store.snapshot_file(
            vault_name="vault", file_path="file2.md", content=content, sync_time=2000
        )

        # Same content -> same hash
        assert hash1 == hash2

        # But different metadata entries
        cursor = snapshot_store.db.execute("SELECT COUNT(*) FROM file_snapshots")
        assert cursor.fetchone()[0] == 2

        # Only one unique content entry
        stats = snapshot_store.get_storage_stats()
        assert stats["unique_content"] == 1


class TestStateManagerIntegration:
    """Tests for StateManager.snapshots property."""

    def test_snapshots_property(self, tmp_path):
        """Test accessing snapshots via StateManager."""
        db_path = tmp_path / "state.db"
        state = StateManager(db_path)

        # Access snapshots property
        snapshots = state.snapshots

        assert isinstance(snapshots, SnapshotStore)

        # Snapshots are usable
        content = b"Test content"
        snapshots.snapshot_file(vault_name="vault", file_path="test.md", content=content)

        # Restore works
        restored = snapshots.restore_file("vault", "test.md")
        assert restored == content

        state.close()

    def test_snapshots_lazy_initialization(self, tmp_path):
        """Test that snapshots are lazily initialized."""
        db_path = tmp_path / "state.db"
        state = StateManager(db_path)

        # Not initialized yet
        assert state._snapshot_store is None

        # Access triggers initialization
        _ = state.snapshots
        assert state._snapshot_store is not None

        # Subsequent access returns same instance
        snapshots1 = state.snapshots
        snapshots2 = state.snapshots
        assert snapshots1 is snapshots2

        state.close()

    def test_snapshot_directory_creation(self, tmp_path):
        """Test that snapshot directory is created automatically."""
        db_path = tmp_path / "state.db"
        state = StateManager(db_path)

        # Access snapshots
        _ = state.snapshots

        # Verify directory created
        snapshots_dir = tmp_path / "snapshots"
        assert snapshots_dir.exists()
        assert snapshots_dir.is_dir()

        state.close()
