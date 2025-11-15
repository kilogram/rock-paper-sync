"""Tests for state management module."""

import hashlib
import time
import pytest
from pathlib import Path

from rock_paper_sync.state import StateError, StateManager, SyncRecord


class TestStateManagerInit:
    """Tests for StateManager initialization."""

    def test_init_creates_database(self, temp_db: Path) -> None:
        """Test that StateManager creates database file."""
        assert not temp_db.exists()
        manager = StateManager(temp_db)
        assert temp_db.exists()
        manager.close()

    def test_init_creates_parent_directory(self, tmp_path: Path) -> None:
        """Test that parent directories are created if needed."""
        db_path = tmp_path / "subdir" / "nested" / "state.db"
        assert not db_path.parent.exists()

        manager = StateManager(db_path)
        assert db_path.exists()
        assert db_path.parent.exists()
        manager.close()

    def test_init_creates_schema(self, temp_db: Path) -> None:
        """Test that database schema is created."""
        manager = StateManager(temp_db)

        # Check that tables exist
        cursor = manager.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}

        expected_tables = {"sync_state", "folder_mapping", "sync_history", "schema_version"}
        assert expected_tables.issubset(tables)

        manager.close()

    def test_init_sets_wal_mode(self, temp_db: Path) -> None:
        """Test that WAL mode is enabled for better concurrency."""
        manager = StateManager(temp_db)
        cursor = manager.conn.execute("PRAGMA journal_mode")
        journal_mode = cursor.fetchone()[0]
        assert journal_mode.upper() == "WAL"
        manager.close()

    def test_init_existing_database(self, temp_db: Path) -> None:
        """Test that opening existing database preserves data."""
        # Create and populate database
        manager1 = StateManager(temp_db)
        record = SyncRecord(
            obsidian_path="test.md",
            remarkable_uuid="uuid-1",
            content_hash="hash-1",
            last_sync_time=12345,
            page_count=1,
            status="synced",
        )
        manager1.update_file_state(record)
        manager1.close()

        # Reopen database
        manager2 = StateManager(temp_db)
        retrieved = manager2.get_file_state("test.md")
        assert retrieved is not None
        assert retrieved.remarkable_uuid == "uuid-1"
        manager2.close()

    def test_init_database_connection_error(self, tmp_path: Path, mocker) -> None:
        """Test that initialization fails gracefully when database connection fails."""
        import sqlite3
        from rock_paper_sync.state import StateError

        # Mock sqlite3.connect to raise an exception
        mocker.patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Test error"))

        db_path = tmp_path / "state.db"
        with pytest.raises(StateError, match="Failed to initialize state database"):
            StateManager(db_path)


class TestFileState:
    """Tests for file state CRUD operations."""

    def test_get_file_state_nonexistent(self, temp_db: Path) -> None:
        """Test getting state for file that hasn't been synced."""
        manager = StateManager(temp_db)
        state = manager.get_file_state("nonexistent.md")
        assert state is None
        manager.close()

    def test_update_and_get_file_state(self, temp_db: Path) -> None:
        """Test inserting and retrieving file state."""
        manager = StateManager(temp_db)

        record = SyncRecord(
            obsidian_path="notes/test.md",
            remarkable_uuid="uuid-123",
            content_hash="abc123def456",
            last_sync_time=1700000000,
            page_count=3,
            status="synced",
        )

        manager.update_file_state(record)
        retrieved = manager.get_file_state("notes/test.md")

        assert retrieved is not None
        assert retrieved.obsidian_path == "notes/test.md"
        assert retrieved.remarkable_uuid == "uuid-123"
        assert retrieved.content_hash == "abc123def456"
        assert retrieved.last_sync_time == 1700000000
        assert retrieved.page_count == 3
        assert retrieved.status == "synced"

        manager.close()

    def test_update_existing_file_state(self, temp_db: Path) -> None:
        """Test that updating existing file replaces old state."""
        manager = StateManager(temp_db)

        # Insert initial state
        record1 = SyncRecord(
            obsidian_path="test.md",
            remarkable_uuid="uuid-1",
            content_hash="hash-1",
            last_sync_time=100,
            page_count=1,
            status="synced",
        )
        manager.update_file_state(record1)

        # Update with new state
        record2 = SyncRecord(
            obsidian_path="test.md",
            remarkable_uuid="uuid-1",  # Same UUID
            content_hash="hash-2",  # Different hash
            last_sync_time=200,
            page_count=2,
            status="synced",
        )
        manager.update_file_state(record2)

        # Should have new state
        retrieved = manager.get_file_state("test.md")
        assert retrieved is not None
        assert retrieved.content_hash == "hash-2"
        assert retrieved.last_sync_time == 200
        assert retrieved.page_count == 2

        manager.close()

    def test_delete_file_state(self, temp_db: Path) -> None:
        """Test deleting file state."""
        manager = StateManager(temp_db)

        record = SyncRecord(
            obsidian_path="to_delete.md",
            remarkable_uuid="uuid-1",
            content_hash="hash-1",
            last_sync_time=100,
            page_count=1,
            status="synced",
        )
        manager.update_file_state(record)

        # Verify it exists
        assert manager.get_file_state("to_delete.md") is not None

        # Delete it
        manager.delete_file_state("to_delete.md")

        # Verify it's gone
        assert manager.get_file_state("to_delete.md") is None

        manager.close()

    def test_get_all_synced_files(self, temp_db: Path) -> None:
        """Test retrieving all synced files."""
        manager = StateManager(temp_db)

        # Insert multiple records
        for i in range(5):
            record = SyncRecord(
                obsidian_path=f"file{i}.md",
                remarkable_uuid=f"uuid-{i}",
                content_hash=f"hash-{i}",
                last_sync_time=i * 1000,
                page_count=i + 1,
                status="synced",
            )
            manager.update_file_state(record)

        all_files = manager.get_all_synced_files()
        assert len(all_files) == 5

        # Check they're all present
        paths = {f.obsidian_path for f in all_files}
        assert paths == {"file0.md", "file1.md", "file2.md", "file3.md", "file4.md"}

        manager.close()


class TestFolderMapping:
    """Tests for folder mapping operations."""

    def test_get_folder_uuid_nonexistent(self, temp_db: Path) -> None:
        """Test getting UUID for folder that hasn't been mapped."""
        manager = StateManager(temp_db)
        uuid = manager.get_folder_uuid("nonexistent/folder")
        assert uuid is None
        manager.close()

    def test_create_and_get_folder_mapping(self, temp_db: Path) -> None:
        """Test creating and retrieving folder mapping."""
        manager = StateManager(temp_db)

        manager.create_folder_mapping("projects/work", "folder-uuid-123")
        retrieved_uuid = manager.get_folder_uuid("projects/work")

        assert retrieved_uuid == "folder-uuid-123"
        manager.close()

    def test_update_folder_mapping(self, temp_db: Path) -> None:
        """Test that updating folder mapping replaces old UUID."""
        manager = StateManager(temp_db)

        manager.create_folder_mapping("folder", "uuid-1")
        manager.create_folder_mapping("folder", "uuid-2")

        retrieved = manager.get_folder_uuid("folder")
        assert retrieved == "uuid-2"

        manager.close()

    def test_multiple_folder_mappings(self, temp_db: Path) -> None:
        """Test managing multiple folder mappings."""
        manager = StateManager(temp_db)

        folders = {
            "projects": "uuid-projects",
            "projects/work": "uuid-work",
            "projects/personal": "uuid-personal",
            "archive": "uuid-archive",
        }

        for folder, uuid in folders.items():
            manager.create_folder_mapping(folder, uuid)

        # Verify all mappings
        for folder, expected_uuid in folders.items():
            retrieved = manager.get_folder_uuid(folder)
            assert retrieved == expected_uuid

        manager.close()


class TestFileHashing:
    """Tests for file content hashing."""

    def test_compute_file_hash(self, tmp_path: Path, temp_db: Path) -> None:
        """Test computing SHA-256 hash of file."""
        manager = StateManager(temp_db)

        test_file = tmp_path / "test.md"
        content = "# Test Document\n\nSome content here."
        test_file.write_text(content)

        computed_hash = manager.compute_file_hash(test_file)

        # Verify it's a valid hex string
        assert len(computed_hash) == 64  # SHA-256 produces 64 hex characters
        assert all(c in "0123456789abcdef" for c in computed_hash)

        # Verify it matches expected hash
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert computed_hash == expected_hash

        manager.close()

    def test_compute_hash_same_content(self, tmp_path: Path, temp_db: Path) -> None:
        """Test that identical content produces same hash."""
        manager = StateManager(temp_db)

        content = "Identical content"
        file1 = tmp_path / "file1.md"
        file2 = tmp_path / "file2.md"

        file1.write_text(content)
        file2.write_text(content)

        hash1 = manager.compute_file_hash(file1)
        hash2 = manager.compute_file_hash(file2)

        assert hash1 == hash2

        manager.close()

    def test_compute_hash_different_content(self, tmp_path: Path, temp_db: Path) -> None:
        """Test that different content produces different hashes."""
        manager = StateManager(temp_db)

        file1 = tmp_path / "file1.md"
        file2 = tmp_path / "file2.md"

        file1.write_text("Content A")
        file2.write_text("Content B")

        hash1 = manager.compute_file_hash(file1)
        hash2 = manager.compute_file_hash(file2)

        assert hash1 != hash2

        manager.close()

    def test_compute_hash_large_file(self, tmp_path: Path, temp_db: Path) -> None:
        """Test hashing large file (tests chunked reading)."""
        manager = StateManager(temp_db)

        large_file = tmp_path / "large.md"
        # Create file larger than chunk size (8192 bytes)
        large_content = "x" * 20000
        large_file.write_text(large_content)

        computed_hash = manager.compute_file_hash(large_file)
        expected_hash = hashlib.sha256(large_content.encode("utf-8")).hexdigest()

        assert computed_hash == expected_hash

        manager.close()

    def test_compute_hash_nonexistent_file(self, tmp_path: Path, temp_db: Path) -> None:
        """Test that hashing nonexistent file raises StateError."""
        manager = StateManager(temp_db)

        nonexistent = tmp_path / "nonexistent.md"

        with pytest.raises(StateError, match="Cannot compute hash"):
            manager.compute_file_hash(nonexistent)

        manager.close()


class TestFindChangedFiles:
    """Tests for finding changed files."""

    def test_find_changed_all_new_files(self, temp_vault: Path, temp_db: Path) -> None:
        """Test finding new files that haven't been synced."""
        manager = StateManager(temp_db)

        # Create test files
        (temp_vault / "file1.md").write_text("Content 1")
        (temp_vault / "file2.md").write_text("Content 2")
        (temp_vault / "notes").mkdir()
        (temp_vault / "notes" / "file3.md").write_text("Content 3")

        changed = manager.find_changed_files(temp_vault, ["**/*.md"], [])

        assert len(changed) == 3
        paths = {f.name for f in changed}
        assert paths == {"file1.md", "file2.md", "file3.md"}

        manager.close()

    def test_find_changed_no_changes(self, temp_vault: Path, temp_db: Path) -> None:
        """Test that unchanged files are not returned."""
        manager = StateManager(temp_db)

        # Create and sync file
        test_file = temp_vault / "test.md"
        test_file.write_text("Content")

        record = SyncRecord(
            obsidian_path="test.md",
            remarkable_uuid="uuid-1",
            content_hash=manager.compute_file_hash(test_file),
            last_sync_time=int(time.time()),
            page_count=1,
            status="synced",
        )
        manager.update_file_state(record)

        # Find changed files
        changed = manager.find_changed_files(temp_vault, ["**/*.md"], [])

        assert len(changed) == 0

        manager.close()

    def test_find_changed_modified_file(self, temp_vault: Path, temp_db: Path) -> None:
        """Test that modified files are detected."""
        manager = StateManager(temp_db)

        # Create and sync file
        test_file = temp_vault / "test.md"
        test_file.write_text("Original content")

        original_hash = manager.compute_file_hash(test_file)
        record = SyncRecord(
            obsidian_path="test.md",
            remarkable_uuid="uuid-1",
            content_hash=original_hash,
            last_sync_time=int(time.time()),
            page_count=1,
            status="synced",
        )
        manager.update_file_state(record)

        # Modify file
        test_file.write_text("Modified content")

        # Find changed files
        changed = manager.find_changed_files(temp_vault, ["**/*.md"], [])

        assert len(changed) == 1
        assert changed[0].name == "test.md"

        manager.close()

    def test_find_changed_respects_include_patterns(self, temp_vault: Path, temp_db: Path) -> None:
        """Test that include patterns are respected."""
        manager = StateManager(temp_db)

        # Create various files
        (temp_vault / "note.md").write_text("Markdown")
        (temp_vault / "doc.txt").write_text("Text")
        (temp_vault / "image.png").write_bytes(b"fake image")

        # Only find .md files
        changed = manager.find_changed_files(temp_vault, ["**/*.md"], [])

        assert len(changed) == 1
        assert changed[0].name == "note.md"

        manager.close()

    def test_find_changed_respects_exclude_patterns(self, temp_vault: Path, temp_db: Path) -> None:
        """Test that exclude patterns are respected."""
        manager = StateManager(temp_db)

        # Create files in various locations
        (temp_vault / "note.md").write_text("Include me")
        (temp_vault / ".obsidian").mkdir()
        (temp_vault / ".obsidian" / "config.md").write_text("Exclude me")
        (temp_vault / "templates").mkdir()
        (temp_vault / "templates" / "template.md").write_text("Exclude me too")

        # Find changed, excluding .obsidian and templates
        changed = manager.find_changed_files(
            temp_vault, ["**/*.md"], [".obsidian/**", "templates/**"]
        )

        assert len(changed) == 1
        assert changed[0].name == "note.md"

        manager.close()

    def test_find_changed_complex_patterns(self, temp_vault: Path, temp_db: Path) -> None:
        """Test complex include/exclude pattern combinations."""
        manager = StateManager(temp_db)

        # Create nested structure
        (temp_vault / "root.md").write_text("Root")
        (temp_vault / "docs").mkdir()
        (temp_vault / "docs" / "public.md").write_text("Public")
        (temp_vault / "docs" / "_private.md").write_text("Private")
        (temp_vault / "archive").mkdir()
        (temp_vault / "archive" / "old.md").write_text("Old")

        # Include all .md, exclude files starting with _ and archive folder
        changed = manager.find_changed_files(
            temp_vault, ["**/*.md"], ["_*", "archive/**"]
        )

        paths = {str(f.relative_to(temp_vault)) for f in changed}
        assert "root.md" in paths
        assert "docs/public.md" in paths
        assert "docs/_private.md" not in paths
        assert "archive/old.md" not in paths

        manager.close()

    def test_find_changed_skips_directories(self, temp_vault: Path, temp_db: Path) -> None:
        """Test that directories are skipped even if they match patterns."""
        manager = StateManager(temp_db)

        # Create directory and file with similar names
        (temp_vault / "test.md").write_text("File content")
        (temp_vault / "folder.md").mkdir()  # Directory with .md extension
        (temp_vault / "folder.md" / "nested.md").write_text("Nested content")

        # Find files - should not include the directory itself
        changed = manager.find_changed_files(temp_vault, ["**/*.md"], [])

        paths = {f.name for f in changed}
        # Should find the files but not the directory "folder.md"
        assert "test.md" in paths
        assert "nested.md" in paths
        # The directory "folder.md" should not be in the results
        assert all(f.is_file() for f in changed)

        manager.close()


class TestSyncHistory:
    """Tests for sync history logging."""

    def test_log_sync_action(self, temp_db: Path) -> None:
        """Test logging a sync action."""
        manager = StateManager(temp_db)

        manager.log_sync_action("test.md", "created", "Generated 3 pages")

        # Verify it was logged
        history = manager.get_recent_history(limit=10)
        assert len(history) == 1
        assert history[0][0] == "test.md"
        assert history[0][1] == "created"
        assert history[0][3] == "Generated 3 pages"

        manager.close()

    def test_log_multiple_actions(self, temp_db: Path) -> None:
        """Test logging multiple actions."""
        import time

        manager = StateManager(temp_db)

        actions = [
            ("file1.md", "created", "New file"),
            ("file2.md", "updated", "Content changed"),
            ("file3.md", "error", "Failed to parse"),
        ]

        for path, action, details in actions:
            manager.log_sync_action(path, action, details)
            time.sleep(0.01)  # Small delay to ensure different timestamps

        history = manager.get_recent_history(limit=10)
        assert len(history) == 3

        # Most recent should be first
        assert history[0][0] == "file3.md"
        assert history[1][0] == "file2.md"
        assert history[2][0] == "file1.md"

        manager.close()

    def test_get_recent_history_limit(self, temp_db: Path) -> None:
        """Test that history respects limit parameter."""
        import time

        manager = StateManager(temp_db)

        # Log many actions
        for i in range(20):
            manager.log_sync_action(f"file{i}.md", "created", "")
            time.sleep(0.001)  # Small delay to ensure different timestamps

        # Request only 5 most recent
        history = manager.get_recent_history(limit=5)
        assert len(history) == 5

        # Should be most recent (highest numbers)
        paths = [h[0] for h in history]
        assert "file19.md" in paths
        assert "file18.md" in paths
        assert "file0.md" not in paths

        manager.close()


class TestStats:
    """Tests for sync statistics."""

    def test_get_stats_empty(self, temp_db: Path) -> None:
        """Test stats for empty database."""
        manager = StateManager(temp_db)
        stats = manager.get_stats()
        assert stats == {}
        manager.close()

    def test_get_stats_single_status(self, temp_db: Path) -> None:
        """Test stats with files in one status."""
        manager = StateManager(temp_db)

        for i in range(3):
            record = SyncRecord(
                obsidian_path=f"file{i}.md",
                remarkable_uuid=f"uuid-{i}",
                content_hash=f"hash-{i}",
                last_sync_time=i,
                page_count=1,
                status="synced",
            )
            manager.update_file_state(record)

        stats = manager.get_stats()
        assert stats == {"synced": 3}

        manager.close()

    def test_get_stats_multiple_statuses(self, temp_db: Path) -> None:
        """Test stats with files in different statuses."""
        manager = StateManager(temp_db)

        statuses = {
            "synced": 5,
            "pending": 2,
            "error": 1,
        }

        count = 0
        for status, num in statuses.items():
            for i in range(num):
                record = SyncRecord(
                    obsidian_path=f"file{count}.md",
                    remarkable_uuid=f"uuid-{count}",
                    content_hash=f"hash-{count}",
                    last_sync_time=count,
                    page_count=1,
                    status=status,
                )
                manager.update_file_state(record)
                count += 1

        stats = manager.get_stats()
        assert stats == statuses

        manager.close()


class TestReset:
    """Tests for resetting sync state."""

    def test_reset_clears_all_data(self, temp_db: Path) -> None:
        """Test that reset clears all sync state."""
        manager = StateManager(temp_db)

        # Add various data
        manager.update_file_state(
            SyncRecord(
                obsidian_path="file.md",
                remarkable_uuid="uuid",
                content_hash="hash",
                last_sync_time=100,
                page_count=1,
                status="synced",
            )
        )
        manager.create_folder_mapping("folder", "folder-uuid")
        manager.log_sync_action("file.md", "created", "")

        # Reset
        manager.reset()

        # Verify all data is gone
        assert manager.get_file_state("file.md") is None
        assert manager.get_folder_uuid("folder") is None
        assert len(manager.get_recent_history()) == 0
        assert manager.get_stats() == {}

        manager.close()


class TestConnectionManagement:
    """Tests for database connection management."""

    def test_close_connection(self, temp_db: Path) -> None:
        """Test closing database connection."""
        manager = StateManager(temp_db)
        manager.close()

        # Attempting to use closed connection should fail
        with pytest.raises(Exception):
            manager.get_file_state("test.md")
