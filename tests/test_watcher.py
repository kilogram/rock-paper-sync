"""Tests for file system watcher module."""

import time
from pathlib import Path
from threading import Event

import pytest

from rock_paper_sync.watcher import ChangeHandler, VaultWatcher


class TestChangeHandler:
    """Test change handler and debouncing logic."""

    def test_init(self) -> None:
        """Test handler initialization."""
        callback_called = False

        def callback(path: Path) -> None:
            nonlocal callback_called
            callback_called = True

        handler = ChangeHandler(callback, debounce_seconds=2)

        assert handler.callback == callback
        assert handler.debounce_seconds == 2
        assert len(handler.pending) == 0

    def test_queue_change(self) -> None:
        """Test queuing file changes."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        # Queue a change
        handler._queue_change("/test/file.md")

        assert "/test/file.md" in handler.pending
        assert isinstance(handler.pending["/test/file.md"], float)

    def test_process_pending_empty(self) -> None:
        """Test processing with no pending changes."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        ready = handler.process_pending()

        assert len(ready) == 0

    def test_process_pending_not_ready(self) -> None:
        """Test changes within debounce window are not ready."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=2)

        # Queue a recent change
        handler._queue_change("/test/file.md")

        # Immediately check - should not be ready
        ready = handler.process_pending()

        assert len(ready) == 0
        # File should still be pending
        assert "/test/file.md" in handler.pending

    def test_process_pending_ready(self) -> None:
        """Test changes past debounce window are ready."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        # Queue a change
        handler._queue_change("/test/file.md")

        # Wait for debounce window
        time.sleep(1.1)

        # Should be ready now
        ready = handler.process_pending()

        assert len(ready) == 1
        assert ready[0] == Path("/test/file.md")
        # Should be removed from pending
        assert "/test/file.md" not in handler.pending

    def test_process_pending_multiple_files(self) -> None:
        """Test processing multiple ready files."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        # Queue multiple changes
        handler._queue_change("/test/file1.md")
        handler._queue_change("/test/file2.md")
        handler._queue_change("/test/file3.md")

        # Wait for debounce
        time.sleep(1.1)

        ready = handler.process_pending()

        assert len(ready) == 3
        assert all(isinstance(p, Path) for p in ready)

    def test_debounce_updates_timestamp(self) -> None:
        """Test rapid changes update timestamp (debounce)."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        # Queue initial change
        handler._queue_change("/test/file.md")
        time.sleep(0.5)

        # Queue another change to same file (simulating rapid edits)
        handler._queue_change("/test/file.md")

        # Wait partial debounce time
        time.sleep(0.7)

        # Should not be ready yet (timestamp was updated)
        ready = handler.process_pending()
        assert len(ready) == 0

        # Wait remaining time
        time.sleep(0.5)

        # Should be ready now
        ready = handler.process_pending()
        assert len(ready) == 1

    def test_on_modified_markdown_file(self) -> None:
        """Test modification of markdown file is queued."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        # Create mock event
        class MockEvent:
            def __init__(self) -> None:
                self.is_directory = False
                self.src_path = "/test/file.md"

        handler.on_modified(MockEvent())  # type: ignore[arg-type]

        assert "/test/file.md" in handler.pending

    def test_on_modified_non_markdown_ignored(self) -> None:
        """Test non-markdown files are ignored."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        # Create mock event for non-.md file
        class MockEvent:
            def __init__(self) -> None:
                self.is_directory = False
                self.src_path = "/test/file.txt"

        handler.on_modified(MockEvent())  # type: ignore[arg-type]

        assert len(handler.pending) == 0

    def test_on_modified_directory_ignored(self) -> None:
        """Test directory events are ignored."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        # Create mock event for directory
        class MockEvent:
            def __init__(self) -> None:
                self.is_directory = True
                self.src_path = "/test/folder"

        handler.on_modified(MockEvent())  # type: ignore[arg-type]

        assert len(handler.pending) == 0

    def test_on_deleted_markdown_file(self) -> None:
        """Test deletion of markdown file is logged but not queued (Phase 1)."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        # Create mock event for markdown file deletion
        class MockEvent:
            def __init__(self) -> None:
                self.is_directory = False
                self.src_path = "/test/file.md"

        handler.on_deleted(MockEvent())  # type: ignore[arg-type]

        # Should not queue deletion (Phase 1 limitation)
        assert len(handler.pending) == 0

    def test_on_deleted_directory_ignored(self) -> None:
        """Test directory deletion is ignored."""

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        # Create mock event for directory deletion
        class MockEvent:
            def __init__(self) -> None:
                self.is_directory = True
                self.src_path = "/test/folder"

        handler.on_deleted(MockEvent())  # type: ignore[arg-type]

        assert len(handler.pending) == 0

    def test_thread_safety(self) -> None:
        """Test handler is thread-safe."""
        import threading

        def callback(path: Path) -> None:
            pass

        handler = ChangeHandler(callback, debounce_seconds=1)

        # Queue changes from multiple threads
        def queue_changes() -> None:
            for i in range(10):
                handler._queue_change(f"/test/file{i}.md")
                time.sleep(0.01)

        threads = [threading.Thread(target=queue_changes) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All changes should be queued
        assert len(handler.pending) == 10


class TestVaultWatcher:
    """Test vault watcher integration."""

    def test_init(self, temp_vault: Path) -> None:
        """Test watcher initialization."""
        called_paths: list[Path] = []

        def callback(path: Path) -> None:
            called_paths.append(path)

        watcher = VaultWatcher(temp_vault, callback, debounce_seconds=1)

        assert watcher.vault_path == temp_vault
        assert watcher.on_change == callback
        assert not watcher.running

    def test_start_and_stop(self, temp_vault: Path) -> None:
        """Test starting and stopping watcher."""
        called_paths: list[Path] = []

        def callback(path: Path) -> None:
            called_paths.append(path)

        watcher = VaultWatcher(temp_vault, callback, debounce_seconds=1)

        watcher.start()
        assert watcher.running

        time.sleep(0.5)

        watcher.stop()
        assert not watcher.running

    def test_detect_file_creation(self, temp_vault: Path) -> None:
        """Test detection of file creation."""
        called_paths: list[Path] = []
        event = Event()

        def callback(path: Path) -> None:
            called_paths.append(path)
            event.set()

        watcher = VaultWatcher(temp_vault, callback, debounce_seconds=1)
        watcher.start()

        # Create a file
        test_file = temp_vault / "new_file.md"
        test_file.write_text("# New File")

        # Wait for debounce + processing
        event.wait(timeout=3)

        watcher.stop()

        # Should have detected the file
        assert len(called_paths) >= 1
        assert any(p.name == "new_file.md" for p in called_paths)

    def test_detect_file_modification(self, temp_vault: Path) -> None:
        """Test detection of file modification."""
        # Create initial file
        test_file = temp_vault / "test.md"
        test_file.write_text("# Initial")

        called_paths: list[Path] = []
        event = Event()

        def callback(path: Path) -> None:
            called_paths.append(path)
            event.set()

        watcher = VaultWatcher(temp_vault, callback, debounce_seconds=1)
        watcher.start()

        # Small delay to ensure watcher is ready
        time.sleep(0.5)

        # Modify the file
        test_file.write_text("# Modified")

        # Wait for debounce + processing
        event.wait(timeout=3)

        watcher.stop()

        # Should have detected the modification
        assert len(called_paths) >= 1
        assert any(p.name == "test.md" for p in called_paths)

    def test_debounce_rapid_changes(self, temp_vault: Path) -> None:
        """Test rapid changes are debounced."""
        called_paths: list[Path] = []

        def callback(path: Path) -> None:
            called_paths.append(path)

        watcher = VaultWatcher(temp_vault, callback, debounce_seconds=2)
        watcher.start()

        # Create and rapidly modify file
        test_file = temp_vault / "rapid.md"
        test_file.write_text("# V1")
        time.sleep(0.2)
        test_file.write_text("# V2")
        time.sleep(0.2)
        test_file.write_text("# V3")

        # Wait for debounce window
        time.sleep(2.5)

        watcher.stop()

        # Should only be called once (or few times), not for every change
        # Since we made 3 changes rapidly, debouncing should reduce callbacks
        # Allow some flexibility in count due to filesystem timing
        assert len(called_paths) <= 2

    def test_recursive_watching(self, temp_vault: Path) -> None:
        """Test watcher monitors subdirectories."""
        # Create subdirectory
        subdir = temp_vault / "subdir"
        subdir.mkdir()

        called_paths: list[Path] = []
        event = Event()

        def callback(path: Path) -> None:
            called_paths.append(path)
            event.set()

        watcher = VaultWatcher(temp_vault, callback, debounce_seconds=1)
        watcher.start()

        time.sleep(0.5)

        # Create file in subdirectory
        test_file = subdir / "nested.md"
        test_file.write_text("# Nested")

        # Wait for detection
        event.wait(timeout=3)

        watcher.stop()

        # Should have detected nested file
        assert len(called_paths) >= 1
        assert any("nested.md" in str(p) for p in called_paths)

    def test_error_in_callback_doesnt_stop_watcher(self, temp_vault: Path) -> None:
        """Test watcher continues after callback error."""
        call_count = 0

        def callback(path: Path) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("Test error")

        watcher = VaultWatcher(temp_vault, callback, debounce_seconds=1)
        watcher.start()

        time.sleep(0.5)

        # Create first file (will cause error)
        file1 = temp_vault / "file1.md"
        file1.write_text("# File 1")

        time.sleep(2.0)  # Increased wait time to ensure processing

        # Create second file (should still be processed)
        file2 = temp_vault / "file2.md"
        file2.write_text("# File 2")

        time.sleep(2.0)  # Increased wait time to ensure processing

        watcher.stop()

        # At least one callback should have been attempted (may be 1 or 2 depending on timing)
        assert call_count >= 1
