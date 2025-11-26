"""Testdata storage and retrieval for device bench.

Manages test artifacts (source markdown, .rm files, manifests) that enable
offline replay of device tests without a physical reMarkable device.

Directory Structure:
    fixtures/testdata/
    ├── collected/           # Auto-captured during online tests
    │   └── {test_id}/
    │       ├── manifest.json
    │       ├── source.md
    │       └── rm_files/
    │           └── {page_uuid}.rm
    └── curated/             # Explicitly extracted test sets
        └── {set_name}/
            └── {test_id}/...
"""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator


@dataclass
class TestManifest:
    """Metadata for a captured test.

    Stored as manifest.json alongside test artifacts.
    """

    test_id: str
    created_at: str  # ISO format timestamp
    doc_uuid: str
    page_uuids: list[str]
    source_document: str  # Original markdown filename
    description: str
    annotations_count: int
    metadata: dict[str, str] = field(default_factory=dict)

    # NEW: Vault snapshot fields for recording vault operations
    vault_snapshots: list[str] = field(default_factory=list)  # e.g., ["initial", "final"]
    vault_operations: list[dict] = field(default_factory=list)  # Recorded file ops
    expected_state_after_unsync: dict = field(default_factory=dict)  # Expected results
    expected_folders_remaining: list[tuple[str, str]] = field(default_factory=list)  # (path, uuid)

    def to_dict(self) -> dict:
        """Serialize manifest to dictionary."""
        return {
            "test_id": self.test_id,
            "created_at": self.created_at,
            "doc_uuid": self.doc_uuid,
            "page_uuids": self.page_uuids,
            "source_document": self.source_document,
            "description": self.description,
            "annotations_count": self.annotations_count,
            "metadata": self.metadata,
            "vault_snapshots": self.vault_snapshots,
            "vault_operations": self.vault_operations,
            "expected_state_after_unsync": self.expected_state_after_unsync,
            "expected_folders_remaining": self.expected_folders_remaining,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestManifest":
        """Deserialize manifest from dictionary."""
        return cls(
            test_id=data["test_id"],
            created_at=data["created_at"],
            doc_uuid=data["doc_uuid"],
            page_uuids=data.get("page_uuids", []),
            source_document=data["source_document"],
            description=data.get("description", ""),
            annotations_count=data.get("annotations_count", 0),
            metadata=data.get("metadata", {}),
            vault_snapshots=data.get("vault_snapshots", []),
            vault_operations=data.get("vault_operations", []),
            expected_state_after_unsync=data.get("expected_state_after_unsync", {}),
            expected_folders_remaining=data.get("expected_folders_remaining", []),
        )


@dataclass
class TestArtifacts:
    """Complete test artifacts for replay.

    Contains everything needed to replay a test in offline mode.
    """

    manifest: TestManifest
    source_markdown: str  # Content of source.md
    rm_files: dict[str, bytes]  # page_uuid -> .rm file content

    @property
    def test_id(self) -> str:
        """Convenience accessor for test ID."""
        return self.manifest.test_id

    @property
    def doc_uuid(self) -> str:
        """Convenience accessor for document UUID."""
        return self.manifest.doc_uuid


class TestdataStore:
    """Manages test artifact storage and retrieval.

    Supports two storage locations:
    - collected/: Auto-captured artifacts from online tests
    - curated/: Manually exported test sets for CI/offline use

    Usage:
        store = TestdataStore(fixtures_dir / "testdata")

        # Save during online test
        store.save_artifacts(test_id, doc_uuid, state, source_path)

        # Load for offline replay
        artifacts = store.load_artifacts("annotation_roundtrip_001")

        # List available tests
        for manifest in store.list_available_tests():
            print(f"{manifest.test_id}: {manifest.description}")
    """

    def __init__(self, base_dir: Path) -> None:
        """Initialize testdata store.

        Args:
            base_dir: Base directory for testdata storage
        """
        self.base_dir = base_dir
        self.collected_dir = base_dir / "collected"
        self.curated_dir = base_dir / "curated"

        # Ensure directories exist
        self.collected_dir.mkdir(parents=True, exist_ok=True)
        self.curated_dir.mkdir(parents=True, exist_ok=True)

    def save_artifacts(
        self,
        test_id: str,
        doc_uuid: str,
        page_uuids: list[str],
        rm_files: dict[str, bytes],
        source_markdown: Path,
        description: str = "",
        metadata: dict[str, str] | None = None,
    ) -> Path:
        """Save test artifacts for later replay.

        Args:
            test_id: Unique test identifier
            doc_uuid: Document UUID from cloud
            page_uuids: List of page UUIDs
            rm_files: Mapping of page_uuid -> .rm bytes
            source_markdown: Path to source markdown file
            description: Human-readable description
            metadata: Additional metadata to store

        Returns:
            Path to the saved test directory
        """
        test_dir = self.collected_dir / test_id
        rm_dir = test_dir / "rm_files"

        # Clean up existing if present
        if test_dir.exists():
            shutil.rmtree(test_dir)

        rm_dir.mkdir(parents=True)

        # Save source markdown
        source_dest = test_dir / "source.md"
        shutil.copy(source_markdown, source_dest)

        # Save .rm files
        for page_uuid, rm_data in rm_files.items():
            rm_path = rm_dir / f"{page_uuid}.rm"
            rm_path.write_bytes(rm_data)

        # Create manifest
        manifest = TestManifest(
            test_id=test_id,
            created_at=datetime.now().isoformat(),
            doc_uuid=doc_uuid,
            page_uuids=page_uuids,
            source_document=source_markdown.name,
            description=description,
            annotations_count=len(rm_files),
            metadata=metadata or {},
        )

        manifest_path = test_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))

        return test_dir

    def load_artifacts(self, test_id: str) -> TestArtifacts:
        """Load previously captured artifacts.

        Searches in both collected/ and curated/ directories.

        Args:
            test_id: Test identifier to load

        Returns:
            Complete test artifacts

        Raises:
            FileNotFoundError: If test_id not found
        """
        # Try collected first, then curated
        test_dir = self._find_test_dir(test_id)

        # Load manifest
        manifest_path = test_dir / "manifest.json"
        manifest_data = json.loads(manifest_path.read_text())
        manifest = TestManifest.from_dict(manifest_data)

        # Load source markdown
        source_path = test_dir / "source.md"
        source_markdown = source_path.read_text()

        # Load .rm files
        rm_dir = test_dir / "rm_files"
        rm_files: dict[str, bytes] = {}

        if rm_dir.exists():
            for rm_file in rm_dir.glob("*.rm"):
                page_uuid = rm_file.stem
                rm_files[page_uuid] = rm_file.read_bytes()

        return TestArtifacts(
            manifest=manifest,
            source_markdown=source_markdown,
            rm_files=rm_files,
        )

    def _find_test_dir(self, test_id: str) -> Path:
        """Find test directory in collected or curated locations.

        Args:
            test_id: Test identifier

        Returns:
            Path to test directory

        Raises:
            FileNotFoundError: If not found
        """
        # Try collected
        collected_path = self.collected_dir / test_id
        if collected_path.exists():
            return collected_path

        # Try curated (any set name)
        for set_dir in self.curated_dir.iterdir():
            if set_dir.is_dir():
                curated_path = set_dir / test_id
                if curated_path.exists():
                    return curated_path

        raise FileNotFoundError(
            f"Test artifacts not found: {test_id}\n"
            f"Searched in: {self.collected_dir}, {self.curated_dir}"
        )

    def list_available_tests(self, include_curated: bool = True) -> list[TestManifest]:
        """List all available test artifacts.

        Args:
            include_curated: Whether to include curated tests

        Returns:
            List of test manifests sorted by test_id
        """
        manifests: list[TestManifest] = []

        # Collected tests
        for test_dir in self._iter_test_dirs(self.collected_dir):
            manifest = self._load_manifest(test_dir)
            if manifest:
                manifests.append(manifest)

        # Curated tests
        if include_curated:
            for set_dir in self.curated_dir.iterdir():
                if set_dir.is_dir():
                    for test_dir in self._iter_test_dirs(set_dir):
                        manifest = self._load_manifest(test_dir)
                        if manifest:
                            manifests.append(manifest)

        return sorted(manifests, key=lambda m: m.test_id)

    def _iter_test_dirs(self, parent: Path) -> Iterator[Path]:
        """Iterate over test directories in a parent directory."""
        if not parent.exists():
            return

        for item in parent.iterdir():
            if item.is_dir() and (item / "manifest.json").exists():
                yield item

    def _load_manifest(self, test_dir: Path) -> TestManifest | None:
        """Load manifest from test directory."""
        manifest_path = test_dir / "manifest.json"
        if not manifest_path.exists():
            return None

        try:
            data = json.loads(manifest_path.read_text())
            return TestManifest.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def export_curated_set(
        self,
        test_ids: list[str],
        set_name: str,
        description: str = "",
    ) -> Path:
        """Export selected tests to a curated set.

        Args:
            test_ids: List of test IDs to export
            set_name: Name for the curated set
            description: Description of the set

        Returns:
            Path to the curated set directory

        Raises:
            FileNotFoundError: If any test_id not found
        """
        set_dir = self.curated_dir / set_name
        set_dir.mkdir(parents=True, exist_ok=True)

        # Write set metadata
        set_manifest = {
            "set_name": set_name,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "test_ids": test_ids,
        }
        (set_dir / "set_manifest.json").write_text(
            json.dumps(set_manifest, indent=2)
        )

        # Copy each test
        for test_id in test_ids:
            source_dir = self._find_test_dir(test_id)
            dest_dir = set_dir / test_id

            if dest_dir.exists():
                shutil.rmtree(dest_dir)

            shutil.copytree(source_dir, dest_dir)

        return set_dir

    def test_exists(self, test_id: str) -> bool:
        """Check if a test exists in storage.

        Args:
            test_id: Test identifier

        Returns:
            True if test artifacts exist
        """
        try:
            self._find_test_dir(test_id)
            return True
        except FileNotFoundError:
            return False

    def delete_test(self, test_id: str) -> bool:
        """Delete a collected test.

        Only deletes from collected/, not curated/.

        Args:
            test_id: Test identifier

        Returns:
            True if deleted, False if not found
        """
        test_dir = self.collected_dir / test_id
        if test_dir.exists():
            shutil.rmtree(test_dir)
            return True
        return False

    def save_vault_snapshot(self, test_id: str, snapshot_name: str, vault_dir: Path) -> Path:
        """Save vault directory snapshot for a test.

        Creates a full copy of the vault directory at a named point.
        Excludes .state, .cache, logs, and .db files.

        Args:
            test_id: Test identifier
            snapshot_name: Name for snapshot (e.g., "initial", "final")
            vault_dir: Path to vault directory to snapshot

        Returns:
            Path to snapshot directory
        """
        snapshot_dir = self.get_snapshot_dir(test_id, snapshot_name)
        snapshot_dir.parent.mkdir(parents=True, exist_ok=True)

        # Copy vault directory, excluding special dirs and databases
        shutil.copytree(
            vault_dir,
            snapshot_dir,
            ignore=shutil.ignore_patterns(".state", ".cache", "logs", "*.db", "config.toml"),
        )

        return snapshot_dir

    def load_vault_snapshot(self, test_id: str, snapshot_name: str) -> Path:
        """Get path to a vault snapshot.

        Args:
            test_id: Test identifier
            snapshot_name: Snapshot name (e.g., "initial", "final")

        Returns:
            Path to snapshot directory

        Raises:
            FileNotFoundError: If snapshot not found
        """
        test_dir = self._find_test_dir(test_id)
        snapshot_dir = test_dir / "vault_snapshots" / snapshot_name

        if not snapshot_dir.exists():
            raise FileNotFoundError(
                f"Snapshot '{snapshot_name}' not found for test '{test_id}'"
            )

        return snapshot_dir

    def save_vault_operations(
        self, test_id: str, operations: list["VaultOperation"]
    ) -> None:
        """Save recorded vault operations for a test.

        Args:
            test_id: Test identifier
            operations: List of vault operations
        """
        test_dir = self.collected_dir / test_id
        ops_file = test_dir / "vault_operations.json"

        ops_data = [op.to_dict() for op in operations]
        ops_file.write_text(json.dumps(ops_data, indent=2))

    def load_vault_operations(self, test_id: str) -> list["VaultOperation"]:
        """Load recorded vault operations for a test.

        Args:
            test_id: Test identifier

        Returns:
            List of vault operations (empty if not found)
        """
        test_dir = self._find_test_dir(test_id)
        ops_file = test_dir / "vault_operations.json"

        if not ops_file.exists():
            return []

        ops_data = json.loads(ops_file.read_text())
        from .vault_manager import VaultOperation

        return [VaultOperation.from_dict(op) for op in ops_data]

    def get_snapshot_dir(self, test_id: str, snapshot_name: str) -> Path:
        """Get path where snapshot should be saved/loaded.

        Args:
            test_id: Test identifier
            snapshot_name: Snapshot name

        Returns:
            Path to snapshot directory
        """
        return self.collected_dir / test_id / "vault_snapshots" / snapshot_name

    def save_golden_vault(self, test_id: str, vault_dir: Path) -> Path:
        """Save final vault state as golden reference during recording.

        Golden vault captures the expected final state after a successful sync.
        Stored in goldens/{test_id}_golden_vault/ directory.

        Args:
            test_id: Test identifier
            vault_dir: Path to vault directory after sync

        Returns:
            Path to golden vault directory
        """
        golden_dir = self.collected_dir / test_id / "goldens" / f"{test_id}_golden_vault"
        golden_dir.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing golden if present
        if golden_dir.exists():
            shutil.rmtree(golden_dir)

        # Copy vault files, excluding system directories
        shutil.copytree(
            vault_dir,
            golden_dir,
            ignore=shutil.ignore_patterns(".state", ".cache", "logs", "*.db", "config.toml"),
        )

        return golden_dir

    def load_golden_vault(self, test_id: str) -> Path:
        """Load golden vault reference for replay test.

        Args:
            test_id: Test identifier

        Returns:
            Path to golden vault directory

        Raises:
            FileNotFoundError: If golden vault not found
        """
        golden_dir = self.collected_dir / test_id / "goldens" / f"{test_id}_golden_vault"

        if not golden_dir.exists():
            raise FileNotFoundError(
                f"Golden vault not found for test '{test_id}'. "
                f"Expected at {golden_dir}"
            )

        return golden_dir
