"""Online device handler for recording real device interactions.

Records test artifacts by prompting user to perform actions on a physical
reMarkable device or rmfakecloud simulation, then capturing the resulting
annotations.

Multi-Phase Support:
    - Saves vault state at each phase (initial, post_sync, final)
    - Prompts user between phases for new annotations
    - Automatically captures .rm files and device state metadata

Usage:
    device = OnlineDevice(workspace, testdata_store, bench)
    device.start_test("pen_colors", description="Write text in different colors")

    doc_uuid = device.upload_document(workspace.test_doc)
    # User sees prompt: "Please annotate document on device, then press Enter"

    state = device.wait_for_annotations(doc_uuid)
    device.end_test("pen_colors", success=True)
    # Testdata saved to tests/testdata/collected/pen_colors/
"""

from pathlib import Path
from typing import TYPE_CHECKING
import json
import shutil
from datetime import datetime

from .protocol import DeviceInteractionManager, DocumentState
from .testdata import TestdataStore, PhaseData

if TYPE_CHECKING:
    from .logging import Bench
    from .workspace import WorkspaceManager


class OnlineDevice(DeviceInteractionManager):
    """Record real device interactions as testdata.

    Prompts user to perform actions on a physical reMarkable device,
    captures the resulting annotations and vault state, and saves them
    as replayable testdata.

    Requirements:
    - Physical reMarkable device OR rmfakecloud simulation
    - Configured cloud credentials
    - User to perform actions on device when prompted

    Usage:
        device = OnlineDevice(workspace, testdata_store, bench)
        device.start_test("test_id", description="Test description")

        doc_uuid = device.upload_document(workspace.test_doc)
        # Displays: "Please annotate on device, then press Enter"

        state = device.wait_for_annotations(doc_uuid)
        device.end_test("test_id", success=True)
    """

    def __init__(
        self,
        workspace: "WorkspaceManager",
        testdata_store: TestdataStore,
        bench: "Bench",
        cloud_url: str = "http://localhost:3000",
    ) -> None:
        """Initialize online device recorder.

        Args:
            workspace: Workspace manager for sync operations
            testdata_store: Store for saving testdata artifacts
            bench: Bench utilities for logging
            cloud_url: Cloud URL (rmfakecloud or real cloud)
        """
        self.workspace = workspace
        self.testdata_store = testdata_store
        self.bench = bench
        self.cloud_url = cloud_url

        # Recording state
        self._current_test_id: str | None = None
        self._current_description: str | None = None
        self._current_phase: int = 0
        self._phases: list[PhaseData] = []

    def start_test(
        self, test_id: str, description: str = ""
    ) -> None:
        """Begin recording a test.

        Creates directory structure and prepares to capture artifacts.

        Args:
            test_id: Unique test identifier
            description: Human-readable test description
        """
        self._current_test_id = test_id
        self._current_description = description
        self._current_phase = 0
        self._phases = []

        # Create testdata directory
        test_dir = self.testdata_store.collected_dir / test_id
        test_dir.mkdir(parents=True, exist_ok=True)

        self.bench.ok(f"Started recording: {test_id}")
        if description:
            self.bench.info(f"Description: {description}")

    def upload_document(self, markdown_path: Path) -> str:
        """Upload document and capture initial phase.

        Args:
            markdown_path: Path to markdown file

        Returns:
            Document UUID

        Raises:
            RuntimeError: If sync fails
        """
        if not self._current_test_id:
            raise RuntimeError("No test started - call start_test() first")

        # Save initial vault state (phase 0)
        self._capture_phase(
            phase_num=0,
            phase_name="initial",
            action="setup",
            prompt="Initial vault state saved"
        )

        # Run sync to upload document
        ret, out, err = self.workspace.run_sync("Upload document")
        if ret != 0:
            self.bench.error(f"Upload failed: {err}")
            self.bench.observe(f"Sync output: {out}")
            raise RuntimeError(f"Failed to upload document: {err}")

        doc_uuid = self.workspace.get_document_uuid()
        if not doc_uuid:
            self.bench.error(f"Document UUID not found after sync")
            self.bench.observe(f"Workspace dir: {self.workspace.workspace_dir}")
            self.bench.observe(f"State dir: {self.workspace.state_dir}")
            if self.workspace.state_dir.exists():
                for item in sorted(self.workspace.state_dir.rglob("*"))[:20]:
                    self.bench.observe(f"  {item.relative_to(self.workspace.state_dir)}")
            raise RuntimeError("Document UUID not found after sync")

        self.bench.ok(f"Uploaded document: {doc_uuid}")

        # Prompt user to wait for document to appear on device
        self.bench.prompt_user(
            "Document uploaded and syncing to your device...",
            "Please wait for the document to appear on your device.",
            "Then press Enter to continue...",
        )

        self._current_phase = 1

        return doc_uuid

    def wait_for_annotations(
        self, doc_uuid: str, timeout: float = 300.0
    ) -> DocumentState:
        """Wait for user to annotate on device, then capture phase.

        Displays user prompt and waits for them to press Enter after
        completing annotations on the physical device. Then waits for the
        device to sync annotations back to cloud before downloading.

        Args:
            doc_uuid: Document UUID
            timeout: Maximum time to wait for annotations to appear (in seconds)

        Returns:
            Document state with captured annotations

        Raises:
            RuntimeError: If sync fails or timeout waiting for annotations
        """
        import time

        if not self._current_test_id:
            raise RuntimeError("No test started - call start_test() first")

        # Prompt user to annotate
        phase_name = f"phase_{self._current_phase}"
        self.bench.prompt_user(
            f"Phase {self._current_phase}: {phase_name}",
            f"Please annotate document on device (doc_uuid: {doc_uuid[:8]})",
            "IMPORTANT: Make sure annotations are synced back to the device",
            "Then press Enter to sync and capture annotations...",
        )

        # Run multiple syncs with small delays to allow device to sync annotations
        # The reMarkable device syncs annotations back to cloud after user annotates
        annotations_found = False
        start_time = time.time()
        attempt = 0
        max_attempts = 5

        while not annotations_found and attempt < max_attempts:
            attempt += 1
            elapsed = time.time() - start_time

            if elapsed > timeout:
                self.bench.warn(f"Timeout waiting for annotations (waited {elapsed:.1f}s)")
                break

            # Run sync
            sync_desc = f"Sync (attempt {attempt}/{max_attempts})"
            ret, out, err = self.workspace.run_sync(sync_desc)
            if ret != 0:
                self.bench.error(f"Sync failed: {err}")
                raise RuntimeError(f"Failed to sync annotations: {err}")

            # Check if annotations were downloaded
            state = self.get_document_state(doc_uuid)
            if state.has_annotations:
                annotations_found = True
                self.bench.ok(f"✓ Annotations found after {attempt} sync(s)")
                break

            if attempt < max_attempts:
                self.bench.observe(f"No annotations yet, waiting 5s before retry...")
                time.sleep(5)

        if not annotations_found:
            self.bench.warn(f"No annotations captured after {attempt} sync attempts")
            self.bench.observe(f"Cache dir: {self.workspace.cache_dir}")
            if self.workspace.cache_dir.exists():
                cache_contents = list(self.workspace.cache_dir.rglob("*"))
                self.bench.observe(f"Cache contains {len(cache_contents)} items")
            self.bench.warn("Common causes:")
            self.bench.warn("1. Device annotations not synced back to cloud yet")
            self.bench.warn("2. No annotations were actually made on the device")
            self.bench.warn("3. rmfakecloud may not support annotation syncing")

        # Capture phase data
        self._capture_phase(
            phase_num=self._current_phase,
            phase_name=phase_name,
            action="annotation_download",
            prompt=f"Annotations captured at phase {self._current_phase}"
        )

        self._current_phase += 1

        return self.get_document_state(doc_uuid)

    def trigger_sync(self) -> None:
        """Run sync command."""
        ret, out, err = self.workspace.run_sync("Sync")
        if ret != 0:
            raise RuntimeError(f"Sync failed: {err}")

    def get_document_state(self, doc_uuid: str) -> DocumentState:
        """Get current document state from local cache.

        Args:
            doc_uuid: Document UUID

        Returns:
            Document state
        """
        rm_files: dict[str, bytes] = {}
        page_uuids: list[str] = []

        cached_files = self.workspace.get_cached_rm_files()
        for rm_path in sorted(cached_files):
            page_uuid = rm_path.stem
            page_uuids.append(page_uuid)
            rm_files[page_uuid] = rm_path.read_bytes()

        has_annotations = len(rm_files) > 0

        if has_annotations:
            self.bench.observe(f"Found {len(rm_files)} .rm file(s)")

        return DocumentState(
            doc_uuid=doc_uuid,
            page_uuids=page_uuids,
            rm_files=rm_files,
            has_annotations=has_annotations,
        )

    def unsync_vault(self, vault_name: str | None = None) -> tuple[int, int]:
        """Unsync vault from cloud.

        Args:
            vault_name: Vault to unsync

        Returns:
            Tuple of (files_removed, files_deleted)
        """
        # Implementation would call actual unsync
        return (0, 0)

    def get_remaining_folders(self, vault_name: str | None = None) -> list[tuple[str, str]]:
        """Get remaining folders after operations.

        Args:
            vault_name: Vault to query

        Returns:
            List of (folder_path, folder_uuid) tuples
        """
        return []

    def end_test(self, test_id: str, success: bool) -> None:
        """Finalize test recording.

        Saves manifest and completes testdata capture.

        Args:
            test_id: Test identifier
            success: Whether test passed
        """
        if not self._current_test_id:
            return

        if success:
            # Save manifest with all phases
            self._save_manifest()
            self.bench.ok(
                f"Recording complete: {test_id} "
                f"({len(self._phases)} phases)"
            )
        else:
            self.bench.warn(f"Test failed: {test_id}")

        self._current_test_id = None
        self._current_description = None
        self._current_phase = 0
        self._phases = []

    def _capture_phase(
        self,
        phase_num: int,
        phase_name: str,
        action: str,
        prompt: str = ""
    ) -> None:
        """Capture vault state for a phase.

        Saves:
        - Vault snapshot (vault files state)
        - Device state metadata (UUIDs, counts)
        - .rm files (annotations)
        - Phase metadata

        Args:
            phase_num: Phase number (0, 1, 2, ...)
            phase_name: Phase name (e.g., "initial", "post_sync")
            action: Action that triggered this phase
            prompt: Logging prompt
        """
        if not self._current_test_id:
            raise RuntimeError("No test started")

        test_dir = self.testdata_store.collected_dir / self._current_test_id
        phase_dir = test_dir / "phases" / f"phase_{phase_num}_{phase_name}"
        phase_dir.mkdir(parents=True, exist_ok=True)

        # Save vault snapshot
        vault_snapshot = phase_dir / "vault_snapshot"
        vault_snapshot.mkdir(parents=True, exist_ok=True)

        workspace_vault = self.workspace.workspace_dir
        if workspace_vault.exists():
            for item in workspace_vault.iterdir():
                if item.name not in [".state", ".cache", "logs", "config.toml"]:
                    if item.is_file():
                        shutil.copy(item, vault_snapshot / item.name)
                    elif item.is_dir():
                        shutil.copytree(item, vault_snapshot / item.name)

        # Save device state metadata
        doc_uuid = self.workspace.get_document_uuid()
        cached_files = self.workspace.get_cached_rm_files()
        page_uuids = [f.stem for f in sorted(cached_files)]

        device_state = {
            "doc_uuid": doc_uuid,
            "page_uuids": page_uuids,
            "rm_files_count": len(cached_files),
            "has_annotations": len(cached_files) > 0,
        }

        (phase_dir / "device_state.json").write_text(
            json.dumps(device_state, indent=2)
        )

        # Save .rm files
        if cached_files:
            rm_dir = phase_dir / "rm_files"
            rm_dir.mkdir(parents=True, exist_ok=True)

            for rm_path in cached_files:
                shutil.copy(rm_path, rm_dir / rm_path.name)

        # Save phase metadata
        phase_info = {
            "phase_number": phase_num,
            "phase_name": phase_name,
            "timestamp": datetime.now().isoformat(),  # Required by PhaseInfo
            "action": action,
            "vault_files": [f.name for f in (vault_snapshot).iterdir() if f.is_file()],
        }

        (phase_dir / "phase_info.json").write_text(
            json.dumps(phase_info, indent=2)
        )

        self.bench.ok(f"Captured phase {phase_num} ({phase_name}): {prompt}")
        self._phases.append(
            PhaseData(
                phase_number=phase_num,
                phase_name=phase_name,
                vault_snapshot_path=vault_snapshot,
                device_state=device_state,
                phase_info=phase_info,
            )
        )

    def _save_manifest(self) -> None:
        """Save manifest with test metadata and phases."""
        if not self._current_test_id:
            return

        test_dir = self.testdata_store.collected_dir / self._current_test_id

        # Prepare source markdown for legacy compatibility
        source_md = self.workspace.test_doc.read_text()
        (test_dir / "source.md").write_text(source_md)

        # Get doc_uuid and page_uuids from the last phase with annotations
        doc_uuid = None
        page_uuids = []
        for phase in reversed(self._phases):
            if phase.device_state and phase.device_state.get("doc_uuid"):
                doc_uuid = phase.device_state["doc_uuid"]
                page_uuids = phase.device_state.get("page_uuids", [])
                break

        # Build manifest
        manifest = {
            "test_id": self._current_test_id,
            "created_at": datetime.now().isoformat(),
            "doc_uuid": doc_uuid or "",  # Required by TestManifest
            "page_uuids": page_uuids,    # Required by TestManifest
            "description": self._current_description or "",
            "annotations_count": len(page_uuids),  # Count of annotated pages
            "source_document": "source.md",
            "phases": [
                {
                    "phase_number": p.phase_number,
                    "phase_name": p.phase_name,
                    "action": p.phase_info.get("action", ""),
                    "vault_files": p.phase_info.get("vault_files", []),
                    "device_state": p.device_state,
                    "has_rm_files": bool(p.device_state and p.device_state.get("rm_files_count", 0) > 0),
                }
                for p in self._phases
            ],
        }

        (test_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2)
        )

        self.bench.ok(f"Saved manifest: {test_dir / 'manifest.json'}")
