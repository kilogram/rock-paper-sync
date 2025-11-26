"""Online vault manager for recording vault operations.

Records all vault file/folder operations for later offline replay.
Prompts user for confirmation before operations.
"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .prompts import user_prompt
from .vault_manager import VaultInteractionManager, VaultOperation

if TYPE_CHECKING:
    from .logging import Bench
    from .testdata import TestdataStore


class OnlineVault(VaultInteractionManager):
    """Vault manager for online (real) vault interaction.

    Records all operations and captures vault state for later replay.

    Features:
    - Prompts user before file/folder operations
    - Records operations for replay
    - Captures vault snapshots for offline testing
    - Manages test lifecycle for artifact capture

    Usage:
        vault = OnlineVault(vault_dir, bench, testdata_store)
        vault.start_test("folder_deletion_001")

        path = vault.create_file("projects/doc.md", "# Content")
        vault.delete_file("projects/doc.md")

        vault.snapshot_vault("final")
        vault.end_test("folder_deletion_001", success=True)
    """

    def __init__(
        self,
        vault_dir: Path,
        bench: "Bench",
        testdata_store: "TestdataStore",
    ) -> None:
        """Initialize online vault manager.

        Args:
            vault_dir: Path to vault directory
            bench: Bench utilities for logging
            testdata_store: Store for capturing artifacts
        """
        self.vault_dir = vault_dir
        self.bench = bench
        self.testdata_store = testdata_store
        self._operations: list[VaultOperation] = []
        self._current_test_id: str | None = None

    def start_test(self, test_id: str) -> None:
        """Begin capturing operations.

        Args:
            test_id: Unique identifier for this test run
        """
        self._current_test_id = test_id
        self._operations.clear()
        self.bench.info(f"Started vault recording: {test_id}")

    def end_test(self, test_id: str, success: bool) -> None:
        """End test and finalize operation capture.

        Args:
            test_id: Test identifier (should match start_test)
            success: Whether the test passed
        """
        if success and self._current_test_id == test_id:
            self.bench.ok(f"Vault test {test_id} completed, operations recorded")
        self._current_test_id = None
        self._operations.clear()

    def create_file(self, rel_path: str, content: str) -> Path:
        """Create a file in the vault.

        Args:
            rel_path: Path relative to vault root
            content: File content

        Returns:
            Absolute path to created file
        """
        # Prompt user for confirmation
        if not user_prompt(
            f"Create file in vault?",
            [f"Path: {rel_path}", f"Preview: {content[:100]}..."],
        ):
            raise RuntimeError(f"User cancelled file creation: {rel_path}")

        # Create the file
        abs_path = self.vault_dir / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content)

        # Record operation
        self._operations.append(
            VaultOperation(
                type="create_file",
                path=rel_path,
                content=content,
                timestamp=datetime.now().isoformat(),
            )
        )

        self.bench.ok(f"Created file: {rel_path}")
        return abs_path

    def delete_file(self, rel_path: str) -> None:
        """Delete a file from the vault.

        Args:
            rel_path: Path relative to vault root

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        abs_path = self.vault_dir / rel_path
        if not abs_path.exists():
            raise FileNotFoundError(f"File not found: {rel_path}")

        # Prompt user for confirmation
        if not user_prompt(
            "Delete file from vault?",
            [f"Path: {rel_path}"],
        ):
            raise RuntimeError(f"User cancelled file deletion: {rel_path}")

        # Delete the file
        abs_path.unlink()

        # Record operation
        self._operations.append(
            VaultOperation(
                type="delete_file",
                path=rel_path,
                timestamp=datetime.now().isoformat(),
            )
        )

        self.bench.ok(f"Deleted file: {rel_path}")

    def create_folder(self, rel_path: str) -> Path:
        """Create a folder in the vault.

        Args:
            rel_path: Path relative to vault root

        Returns:
            Absolute path to created folder
        """
        # Prompt user for confirmation
        if not user_prompt(
            "Create folder in vault?",
            [f"Path: {rel_path}"],
        ):
            raise RuntimeError(f"User cancelled folder creation: {rel_path}")

        # Create the folder
        abs_path = self.vault_dir / rel_path
        abs_path.mkdir(parents=True, exist_ok=True)

        # Record operation
        self._operations.append(
            VaultOperation(
                type="create_folder",
                path=rel_path,
                timestamp=datetime.now().isoformat(),
            )
        )

        self.bench.ok(f"Created folder: {rel_path}")
        return abs_path

    def delete_folder(self, rel_path: str) -> None:
        """Delete a folder from the vault.

        Args:
            rel_path: Path relative to vault root

        Raises:
            FileNotFoundError: If folder doesn't exist
        """
        abs_path = self.vault_dir / rel_path
        if not abs_path.exists():
            raise FileNotFoundError(f"Folder not found: {rel_path}")

        # Prompt user for confirmation
        if not user_prompt(
            "Delete folder from vault?",
            [f"Path: {rel_path}"],
        ):
            raise RuntimeError(f"User cancelled folder deletion: {rel_path}")

        # Delete the folder
        shutil.rmtree(abs_path)

        # Record operation
        self._operations.append(
            VaultOperation(
                type="delete_folder",
                path=rel_path,
                timestamp=datetime.now().isoformat(),
            )
        )

        self.bench.ok(f"Deleted folder: {rel_path}")

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
        """Capture current vault state to snapshot.

        Args:
            name: Snapshot name (e.g., "initial", "final")

        Returns:
            Path to snapshot directory

        Raises:
            RuntimeError: If test not active or snapshot fails
        """
        if not self._current_test_id:
            raise RuntimeError("No test active - call start_test first")

        snapshot_dir = self.testdata_store.get_snapshot_dir(self._current_test_id, name)
        snapshot_dir.parent.mkdir(parents=True, exist_ok=True)

        # Copy entire vault directory, excluding special dirs and databases
        shutil.copytree(
            self.vault_dir,
            snapshot_dir,
            ignore=shutil.ignore_patterns(".state", ".cache", "logs", "*.db", "config.toml"),
        )

        self.bench.ok(f"Captured vault snapshot: {name}")
        return snapshot_dir

    def restore_vault(self, name: str) -> None:
        """Restore vault from snapshot.

        In online mode, this is a no-op since we don't replay snapshots.

        Args:
            name: Snapshot name to restore
        """
        # Online mode doesn't restore - it records instead
        pass
