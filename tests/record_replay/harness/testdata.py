"""Testdata storage and retrieval for device bench.

Manages test artifacts (source markdown, .rm files, manifests) that enable
offline replay of device tests without a physical reMarkable device.

Directory Structure (Multi-Phase):
    fixtures/testdata/
    └── {test_id}/
        ├── manifest.json       # Enhanced with phases array
        ├── phases/             # Multi-phase structure
        │   ├── phase_0_initial/
        │   │   ├── vault_snapshot/
        │   │   └── phase_info.json
        │   ├── phase_1_post_sync/
        │   │   ├── vault_snapshot/
        │   │   ├── device_state.json
        │   │   ├── rm_files/
        │   │   └── phase_info.json
        │   └── phase_2_final/
        │       ├── vault_snapshot/
        │       └── phase_info.json
        └── goldens/            # Co-located golden references
            └── final_vault/
"""

import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .vault_manager import VaultOperation


@dataclass
class PhaseData:
    """Complete data for a single test phase.

    Represents the state and artifacts at a specific point in test execution.
    """

    phase_number: int
    phase_name: str  # e.g., "initial", "post_sync", "final"
    vault_snapshot_path: Path  # Path to vault_snapshot directory
    device_state: dict | None = None  # Device metadata (UUIDs, counts, etc.)
    rm_files: dict[str, bytes] = field(default_factory=dict)  # page_uuid -> .rm data
    phase_info: dict = field(default_factory=dict)  # Full phase metadata


@dataclass
class PhaseInfo:
    """Metadata stored in phase_info.json for each phase."""

    phase_number: int
    phase_name: str
    timestamp: str  # ISO format
    action: str  # e.g., "setup", "sync_upload", "sync_download", "teardown"
    description: str = ""
    vault_hash: str = ""  # SHA256 hash of vault state
    device_state: dict | None = None

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "phase_number": self.phase_number,
            "phase_name": self.phase_name,
            "timestamp": self.timestamp,
            "action": self.action,
            "description": self.description,
            "vault_hash": self.vault_hash,
            "device_state": self.device_state,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PhaseInfo":
        """Deserialize from dictionary."""
        return cls(
            phase_number=data["phase_number"],
            phase_name=data["phase_name"],
            timestamp=data["timestamp"],
            action=data["action"],
            description=data.get("description", ""),
            vault_hash=data.get("vault_hash", ""),
            device_state=data.get("device_state"),
        )


@dataclass
class TestManifest:
    """Metadata for a captured test.

    Stored as manifest.json alongside test artifacts.
    Supports both legacy single-phase and new multi-phase testdata.
    """

    test_id: str
    created_at: str  # ISO format timestamp
    doc_uuid: str
    page_uuids: list[str]
    source_document: str  # Original markdown filename
    description: str
    annotations_count: int
    metadata: dict[str, str] = field(default_factory=dict)

    # Multi-phase support: NEW primary structure
    phases: list[dict] = field(default_factory=list)  # Phase metadata array
    # Each phase dict contains:
    # {
    #   "phase_number": int,
    #   "phase_name": str,
    #   "description": str,
    #   "action": str,
    #   "vault_files": list[str],
    #   "device_state": dict | None,
    #   "has_rm_files": bool
    # }

    # Legacy vault snapshot fields (for backward compatibility)
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
            "phases": self.phases,  # Multi-phase structure
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
            phases=data.get("phases", []),  # Multi-phase structure
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
    All tests are stored directly under testdata/ in flat structure

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

        # Ensure base directory exists
        self.base_dir.mkdir(parents=True, exist_ok=True)

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
        test_dir = self.base_dir / test_id
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

        Searches in testdata directory.

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
        """Find test directory.

        Args:
            test_id: Test identifier

        Returns:
            Path to test directory

        Raises:
            FileNotFoundError: If not found
        """
        test_path = self.base_dir / test_id
        if test_path.exists():
            return test_path

        raise FileNotFoundError(
            f"Test artifacts not found: {test_id}\n" f"Searched in: {self.base_dir}"
        )

    def list_available_tests(self, include_curated: bool = True) -> list[TestManifest]:
        """List all available test artifacts.

        Args:
            include_curated: Unused (kept for API compatibility)

        Returns:
            List of test manifests sorted by test_id
        """
        manifests: list[TestManifest] = []

        # Scan base directory for all tests
        for test_dir in self._iter_test_dirs(self.base_dir):
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
        set_dir = self.base_dir / set_name
        set_dir.mkdir(parents=True, exist_ok=True)

        # Write set metadata
        set_manifest = {
            "set_name": set_name,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "test_ids": test_ids,
        }
        (set_dir / "set_manifest.json").write_text(json.dumps(set_manifest, indent=2))

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

        Deletes test directory from testdata/.

        Args:
            test_id: Test identifier

        Returns:
            True if deleted, False if not found
        """
        test_dir = self.base_dir / test_id
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
            raise FileNotFoundError(f"Snapshot '{snapshot_name}' not found for test '{test_id}'")

        return snapshot_dir

    def save_vault_operations(self, test_id: str, operations: list["VaultOperation"]) -> None:
        """Save recorded vault operations for a test.

        Args:
            test_id: Test identifier
            operations: List of vault operations
        """
        test_dir = self.base_dir / test_id
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
        return self.base_dir / test_id / "vault_snapshots" / snapshot_name

    def save_golden_vault(self, test_id: str, vault_dir: Path, phase_name: str = "final") -> Path:
        """Save vault state as golden reference during recording.

        Golden vault captures the expected state after a specific phase.
        Stored in goldens/{phase_name}_vault/ directory.

        Args:
            test_id: Test identifier
            vault_dir: Path to vault directory after sync
            phase_name: Phase name for this golden (default: "final")

        Returns:
            Path to golden vault directory
        """
        golden_dir = self.base_dir / test_id / "goldens" / f"{phase_name}_vault"
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

    def load_golden_vault(self, test_id: str, phase_name: str = "final") -> Path:
        """Load golden vault reference for replay test.

        Args:
            test_id: Test identifier
            phase_name: Phase name for this golden (default: "final")

        Returns:
            Path to golden vault directory

        Raises:
            FileNotFoundError: If golden vault not found
        """
        golden_dir = self.base_dir / test_id / "goldens" / f"{phase_name}_vault"

        if not golden_dir.exists():
            raise FileNotFoundError(
                f"Golden vault not found for test '{test_id}' phase '{phase_name}'. "
                f"Expected at {golden_dir}"
            )

        return golden_dir

    # =========================================================================
    # Multi-Phase Support Methods
    # =========================================================================

    def load_phases(self, test_id: str) -> list[PhaseData]:
        """Load all phases for a test artifact.

        Supports both multi-phase (new structure) and legacy single-phase tests.
        Automatically migrates legacy tests to multi-phase on first load.

        Args:
            test_id: Test identifier

        Returns:
            List of PhaseData objects sorted by phase_number

        Raises:
            FileNotFoundError: If test not found
        """
        test_dir = self._find_test_dir(test_id)
        phases_dir = test_dir / "phases"

        # If no phases directory, check if this is legacy and migrate
        if not phases_dir.exists():
            return self._migrate_legacy_test(test_id)

        # Load all phases from phases directory
        phases: list[PhaseData] = []
        for phase_dir in sorted(phases_dir.iterdir()):
            if not phase_dir.is_dir():
                continue

            phase_info_file = phase_dir / "phase_info.json"
            if not phase_info_file.exists():
                continue

            # Load phase metadata
            phase_info_data = json.loads(phase_info_file.read_text())
            phase_info = PhaseInfo.from_dict(phase_info_data)

            # Load vault snapshot
            vault_snapshot_path = phase_dir / "vault_snapshot"

            # Load device state if present
            device_state = None
            device_state_file = phase_dir / "device_state.json"
            if device_state_file.exists():
                device_state = json.loads(device_state_file.read_text())

            # Load rm files if present
            rm_files: dict[str, bytes] = {}
            rm_dir = phase_dir / "rm_files"
            if rm_dir.exists():
                for rm_file in rm_dir.glob("*.rm"):
                    rm_files[rm_file.stem] = rm_file.read_bytes()

            phases.append(
                PhaseData(
                    phase_number=phase_info.phase_number,
                    phase_name=phase_info.phase_name,
                    vault_snapshot_path=vault_snapshot_path,
                    device_state=device_state,
                    rm_files=rm_files,
                    phase_info=phase_info.to_dict(),
                )
            )

        return sorted(phases, key=lambda p: p.phase_number)

    def get_phase_dir(self, test_id: str, phase_num: int, phase_name: str) -> Path:
        """Get directory path for a specific phase.

        Creates the directory if it doesn't exist.

        Args:
            test_id: Test identifier
            phase_num: Phase number
            phase_name: Phase name (e.g., "initial", "post_sync", "final")

        Returns:
            Path to phase directory
        """
        test_dir = self.base_dir / test_id
        phase_dir = test_dir / "phases" / f"phase_{phase_num}_{phase_name}"
        return phase_dir

    def _migrate_legacy_test(self, test_id: str) -> list[PhaseData]:
        """Migrate legacy single-phase test to multi-phase structure.

        Converts old test format (manifest.json, source.md, rm_files/) to new
        multi-phase format (phases/phase_0_initial, phases/phase_1_final, etc.)

        Legacy structure:
            test_id/
            ├── manifest.json
            ├── source.md
            └── rm_files/
                ├── page1.rm
                └── page2.rm

        New structure:
            test_id/
            ├── manifest.json (updated with phases array)
            └── phases/
                ├── phase_0_initial/
                │   ├── vault_snapshot/
                │   │   └── source.md
                │   └── phase_info.json
                └── phase_1_final/
                    ├── vault_snapshot/
                    │   └── source.md
                    ├── rm_files/
                    │   ├── page1.rm
                    │   └── page2.rm
                    └── phase_info.json

        Args:
            test_id: Test identifier to migrate

        Returns:
            List of PhaseData objects for migrated test

        Raises:
            FileNotFoundError: If test not found
        """
        test_dir = self._find_test_dir(test_id)

        # Check if already migrated
        if (test_dir / "phases").exists():
            return self.load_phases(test_id)

        # Read legacy manifest
        manifest_path = test_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found for test {test_id}")

        manifest_data = json.loads(manifest_path.read_text())

        # Create phases directory
        phases_dir = test_dir / "phases"
        phases_dir.mkdir(exist_ok=True)

        phases_metadata: list[dict] = []

        # Phase 0: Initial state
        phase0_dir = phases_dir / "phase_0_initial"
        phase0_dir.mkdir(exist_ok=True)
        vault_snapshot_dir = phase0_dir / "vault_snapshot"
        vault_snapshot_dir.mkdir(exist_ok=True)

        # Copy source.md to phase 0 vault snapshot
        source_path = test_dir / "source.md"
        if source_path.exists():
            shutil.copy(source_path, vault_snapshot_dir / "source.md")

        # Save phase 0 info
        phase0_info = PhaseInfo(
            phase_number=0,
            phase_name="initial",
            timestamp=manifest_data.get("created_at", datetime.now().isoformat()),
            action="setup",
            description="Legacy test initial state (migrated)",
            vault_hash="",
            device_state=None,
        )
        (phase0_dir / "phase_info.json").write_text(json.dumps(phase0_info.to_dict(), indent=2))

        phases_metadata.append(
            {
                "phase_number": 0,
                "phase_name": "initial",
                "description": "Legacy initial state",
                "action": "setup",
                "vault_files": ["source.md"],
                "device_state": None,
                "has_rm_files": False,
            }
        )

        # Phase 1: Final state (with rm files)
        phase1_dir = phases_dir / "phase_1_final"
        phase1_dir.mkdir(exist_ok=True)
        vault_snapshot_dir = phase1_dir / "vault_snapshot"
        vault_snapshot_dir.mkdir(exist_ok=True)

        # Copy source.md to phase 1 vault snapshot
        if source_path.exists():
            shutil.copy(source_path, vault_snapshot_dir / "source.md")

        # Move legacy rm_files to phase 1
        legacy_rm_dir = test_dir / "rm_files"
        has_rm_files = False
        if legacy_rm_dir.exists():
            shutil.move(legacy_rm_dir, phase1_dir / "rm_files")
            has_rm_files = True

        # Save device state to phase 1
        device_state = {
            "doc_uuid": manifest_data.get("doc_uuid"),
            "page_uuids": manifest_data.get("page_uuids", []),
            "rm_files_count": manifest_data.get("annotations_count", 0),
            "has_annotations": manifest_data.get("annotations_count", 0) > 0,
        }
        (phase1_dir / "device_state.json").write_text(json.dumps(device_state, indent=2))

        # Save phase 1 info
        phase1_info = PhaseInfo(
            phase_number=1,
            phase_name="final",
            timestamp=manifest_data.get("created_at", datetime.now().isoformat()),
            action="annotation_download",
            description="Legacy test final state (migrated)",
            vault_hash="",
            device_state=device_state,
        )
        (phase1_dir / "phase_info.json").write_text(json.dumps(phase1_info.to_dict(), indent=2))

        phases_metadata.append(
            {
                "phase_number": 1,
                "phase_name": "final",
                "description": "Legacy final state",
                "action": "annotation_download",
                "vault_files": ["source.md"],
                "device_state": device_state,
                "has_rm_files": has_rm_files,
            }
        )

        # Update manifest with phases
        manifest_data["phases"] = phases_metadata
        manifest_data["metadata"] = manifest_data.get("metadata", {})
        manifest_data["metadata"]["migrated_from_legacy"] = True
        manifest_path.write_text(json.dumps(manifest_data, indent=2))

        # Return the migrated phases
        return self.load_phases(test_id)
