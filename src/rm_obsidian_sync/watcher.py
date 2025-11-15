"""File system watcher for Obsidian vault changes.

Monitors a directory tree for markdown file changes and triggers sync callbacks.
Uses watchdog library with debouncing to avoid duplicate processing.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger("rm_obsidian_sync.watcher")


class ChangeHandler(FileSystemEventHandler):
    """Handles file system events and debounces rapid changes.

    Rapid changes to the same file (e.g., during editing) are coalesced
    into a single callback after the debounce period.
    """

    def __init__(
        self, callback: Callable[[Path], None], debounce_seconds: int = 5
    ) -> None:
        """Initialize change handler.

        Args:
            callback: Function to call with Path when file changes
            debounce_seconds: Seconds to wait before processing a change
        """
        super().__init__()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.pending: dict[str, float] = {}
        self.lock = threading.Lock()

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification events.

        Args:
            event: File system event from watchdog
        """
        if not event.is_directory and event.src_path.endswith(".md"):
            logger.debug(f"File modified: {event.src_path}")
            self._queue_change(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file creation events.

        Args:
            event: File system event from watchdog
        """
        if not event.is_directory and event.src_path.endswith(".md"):
            logger.debug(f"File created: {event.src_path}")
            self._queue_change(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        """Handle file deletion events.

        Args:
            event: File system event from watchdog

        Note:
            Deletion handling is a Phase 2 feature. Currently just logged.
        """
        if not event.is_directory and event.src_path.endswith(".md"):
            logger.debug(f"File deleted: {event.src_path} (deletion not yet supported)")
            # TODO: Implement deletion in Phase 2

    def _queue_change(self, path: str) -> None:
        """Add or update pending change timestamp.

        Thread-safe operation to track when a file was last changed.

        Args:
            path: Absolute path to changed file
        """
        with self.lock:
            self.pending[path] = time.time()

    def process_pending(self) -> list[Path]:
        """Check for changes past debounce window.

        Returns files that haven't been modified for at least debounce_seconds.
        Removes returned files from pending queue.

        Returns:
            List of Path objects ready to be processed

        Note:
            This method is thread-safe and can be called from multiple threads.
        """
        ready = []
        now = time.time()

        with self.lock:
            expired = []
            for path, timestamp in self.pending.items():
                if now - timestamp >= self.debounce_seconds:
                    ready.append(Path(path))
                    expired.append(path)

            for path in expired:
                del self.pending[path]

        return ready


class VaultWatcher:
    """Monitors Obsidian vault directory for markdown file changes.

    Uses watchdog Observer to detect file system events and debounces
    rapid changes to avoid duplicate processing.
    """

    def __init__(
        self,
        vault_path: Path,
        on_change: Callable[[Path], None],
        debounce_seconds: int = 5,
    ) -> None:
        """Initialize vault watcher.

        Args:
            vault_path: Path to Obsidian vault root directory
            on_change: Callback function called with Path when file is ready to sync
            debounce_seconds: Seconds to wait before processing changes (default 5)
        """
        self.vault_path = vault_path
        self.on_change = on_change
        self.handler = ChangeHandler(on_change, debounce_seconds)
        self.observer = Observer()
        self.running = False
        self._process_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start watching vault directory.

        Spawns two threads:
        1. Watchdog observer thread (detects file events)
        2. Processing thread (checks for debounced changes)

        Both threads are daemon threads that will exit when main thread exits.
        """
        logger.info(f"Starting file watcher on {self.vault_path}")

        # Schedule recursive monitoring
        self.observer.schedule(self.handler, str(self.vault_path), recursive=True)
        self.observer.start()
        self.running = True

        # Start pending change processor thread
        self._process_thread = threading.Thread(
            target=self._process_loop, daemon=True
        )
        self._process_thread.start()

        logger.debug("File watcher threads started")

    def _process_loop(self) -> None:
        """Continuously check for pending changes and process ready files.

        Runs in separate thread. Checks every second for files that have
        passed the debounce window and calls the callback for each.

        Errors in callbacks are caught and logged but don't stop the loop.
        """
        while self.running:
            ready = self.handler.process_pending()
            for path in ready:
                try:
                    logger.info(f"Processing change: {path}")
                    self.on_change(path)
                except Exception as e:
                    logger.error(f"Error processing {path}: {e}", exc_info=True)
            time.sleep(1)

    def stop(self) -> None:
        """Stop watching and clean up threads.

        Gracefully shuts down both the observer and processing threads.
        Waits up to 5 seconds for processing thread to finish.
        """
        logger.info("Stopping file watcher")
        self.running = False
        self.observer.stop()
        self.observer.join()
        if self._process_thread:
            self._process_thread.join(timeout=5)
        logger.debug("File watcher stopped")
