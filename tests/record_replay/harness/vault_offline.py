"""Offline vault manager for replaying vault snapshots.

Restores vault state from pre-recorded snapshots without user interaction.
"""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .vault_manager import VaultInteractionManager

if TYPE_CHECKING:
    from .bench import Bench
    from .testdata import TestdataStore


class OfflineVault(VaultInteractionManager):
    """Vault manager for offline (replay) testing.

    Restores vault state from pre-captured snapshots without user interaction.
    Operations are silent since data comes from pre-recorded testdata.

    Features:
    - Restores vault from snapshots (no user prompts)
    - File/folder operations performed silently
    - No artifact capture (already in testdata)
    - Enables CI testing without user interaction

    Usage:
        vault = OfflineVault(vault_dir, bench, testdata_store)
        vault.load_test("folder_deletion_001")

        vault.restore_vault("initial")
        # ... perform test operations ...
        vault.snapshot_vault("final")  # No-op in offline mode
    """

    def __init__(
        self,
        vault_dir: Path,
        bench: "Bench",
        testdata_store: "TestdataStore",
    ) -> None:
        """Initialize offline vault manager.

        Args:
            vault_dir: Path to vault directory
            bench: Bench utilities for logging
            testdata_store: Store for loading testdata
        """
        self.vault_dir = vault_dir
        self.bench = bench
        self.testdata_store = testdata_store
        self._current_test_id: str | None = None

    def load_test(self, test_id: str) -> None:
        """Load test for offline replay.

        Args:
            test_id: Test identifier to load
        """
        self._current_test_id = test_id
        self.bench.info(f"Loaded offline vault test: {test_id}")

    def start_test(self, test_id: str) -> None:
        """Begin offline vault test.

        Loads test if not already loaded.

        Args:
            test_id: Test identifier
        """
        if self._current_test_id != test_id:
            self.load_test(test_id)
        self.bench.info(f"Started offline vault test: {test_id}")

    def end_test(self, test_id: str, success: bool) -> None:
        """End offline test.

        Args:
            test_id: Test identifier
            success: Whether test passed
        """
        if success:
            self.bench.ok(f"Offline vault test {test_id} completed")
        self._current_test_id = None

    def create_file(self, rel_path: str, content: str) -> Path:
        """Create a file in the vault (offline, silent).

        Args:
            rel_path: Path relative to vault root
            content: File content

        Returns:
            Absolute path to created file
        """
        # No prompts in offline mode - just create silently
        abs_path = self.vault_dir / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content)

        self.bench.info(f"Created file (offline): {rel_path}")
        return abs_path

    def delete_file(self, rel_path: str) -> None:
        """Delete a file from the vault (offline, silent).

        Args:
            rel_path: Path relative to vault root

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        abs_path = self.vault_dir / rel_path
        if not abs_path.exists():
            raise FileNotFoundError(f"File not found: {rel_path}")

        abs_path.unlink()
        self.bench.info(f"Deleted file (offline): {rel_path}")

    def create_folder(self, rel_path: str) -> Path:
        """Create a folder in the vault (offline, silent).

        Args:
            rel_path: Path relative to vault root

        Returns:
            Absolute path to created folder
        """
        abs_path = self.vault_dir / rel_path
        abs_path.mkdir(parents=True, exist_ok=True)

        self.bench.info(f"Created folder (offline): {rel_path}")
        return abs_path

    def delete_folder(self, rel_path: str) -> None:
        """Delete a folder from the vault (offline, silent).

        Args:
            rel_path: Path relative to vault root

        Raises:
            FileNotFoundError: If folder doesn't exist
        """
        abs_path = self.vault_dir / rel_path
        if not abs_path.exists():
            raise FileNotFoundError(f"Folder not found: {rel_path}")

        shutil.rmtree(abs_path)
        self.bench.info(f"Deleted folder (offline): {rel_path}")

    def get_vault_state(self) -> dict[str, Any]:
        """Get current vault file structure.

        Returns:
            Dict with files, folders, and total_size
        """
        files = []
        folders = []
        total_size = 0

        for item in self.vault_dir.rglob("*"):
            # Skip special directories
            if any(part in item.parts for part in {".state", ".cache", "logs"}):
                continue

            rel_path = item.relative_to(self.vault_dir)

            if item.is_file():
                files.append(str(rel_path))
                total_size += item.stat().st_size
            elif item.is_dir():
                folders.append(str(rel_path))

        return {
            "files": files,
            "folders": folders,
            "total_size": total_size,
        }

    def snapshot_vault(self, name: str) -> Path:
        """In offline mode, snapshots are not saved (already in testdata).

        Args:
            name: Snapshot name

        Returns:
            Empty path (no-op in offline mode)
        """
        self.bench.info(f"Skipping vault snapshot in offline mode: {name}")
        return Path()

    def restore_vault(self, name: str) -> None:
        """Restore vault from pre-captured snapshot.

        Args:
            name: Snapshot name to restore (e.g., "initial", "final")

        Raises:
            RuntimeError: If test not loaded or snapshot not found
        """
        if not self._current_test_id:
            raise RuntimeError("No test loaded - call load_test first")

        snapshot_dir = self.testdata_store.load_vault_snapshot(
            self._current_test_id, name
        )

        # Clear vault directory (except special dirs)
        special_dirs = {".state", ".cache", "logs", "config.toml"}
        for item in self.vault_dir.iterdir():
            if item.name not in special_dirs:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

        # Restore from snapshot
        for item in snapshot_dir.iterdir():
            dest = self.vault_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        self.bench.ok(f"Restored vault from snapshot: {name}")
