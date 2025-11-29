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
    # Testdata saved to tests/testdata/pen_colors/
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .protocol import DeviceInteractionManager, DocumentState, derive_test_id
from .testdata import PhaseData, TestdataStore

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
    ) -> None:
        """Initialize online device recorder.

        Args:
            workspace: Workspace manager for sync operations (provides cloud_url)
            testdata_store: Store for saving testdata artifacts
            bench: Bench utilities for logging
        """
        self.workspace = workspace
        self.testdata_store = testdata_store
        self.bench = bench

        # Recording state
        self._current_test_id: str | None = None
        self._current_description: str | None = None
        self._current_phase: int = 0
        self._phases: list[PhaseData] = []

    def start_test(self, test_id: str, description: str = "") -> None:
        """Begin recording a test.

        Creates directory structure and prepares to capture artifacts.
        Automatically cleans up any existing testdata for this test_id
        (git can restore if needed).

        Args:
            test_id: Unique test identifier
            description: Human-readable test description
        """
        self._current_test_id = test_id
        self._current_description = description
        self._current_phase = 0
        self._phases = []

        # Auto-cleanup existing testdata (git can restore if needed)
        test_dir = self.testdata_store.base_dir / test_id
        if test_dir.exists():
            shutil.rmtree(test_dir)
            self.bench.info(f"Cleaned up existing testdata: {test_id}")

        # Create fresh testdata directory
        test_dir.mkdir(parents=True, exist_ok=True)

        self.bench.ok(f"Started recording: {test_id}")
        if description:
            self.bench.info(f"Description: {description}")

    def start_test_for_fixture(self, fixture_path: Path, description: str = "") -> str:
        """Begin recording a test, deriving test_id from fixture path.

        This is the preferred way to start tests as it ensures test_id
        matches the fixture, avoiding redundant recordings.

        Args:
            fixture_path: Path to the fixture markdown file
            description: Human-readable test description

        Returns:
            The derived test_id
        """
        test_id = derive_test_id(fixture_path)
        self.start_test(test_id, description)
        return test_id

    def upload_document(self, markdown_path: Path) -> str:
        """Upload document and capture initial phase.

        Captures two phases:
        - phase_0 "initial": Vault state before sync (no rm files)
        - phase_1 "post_upload": State after sync (rm files we uploaded)

        Args:
            markdown_path: Path to markdown file

        Returns:
            Document UUID

        Raises:
            RuntimeError: If sync fails
        """
        if not self._current_test_id:
            raise RuntimeError("No test started - call start_test() first")

        # Save initial vault state (phase 0) - before any sync
        self._capture_phase(
            phase_num=0, phase_name="initial", action="setup", prompt="Initial vault state saved"
        )

        # Run sync to upload document
        ret, out, err = self.workspace.run_sync("Upload document")
        if ret != 0:
            self.bench.error(f"Upload failed: {err}")
            self.bench.observe(f"Sync output: {out}")
            raise RuntimeError(f"Failed to upload document: {err}")

        doc_uuid = self.workspace.get_document_uuid()
        if not doc_uuid:
            self.bench.error("Document UUID not found after sync")
            self.bench.observe(f"Workspace dir: {self.workspace.workspace_dir}")
            self.bench.observe(f"State dir: {self.workspace.state_dir}")
            if self.workspace.state_dir.exists():
                for item in sorted(self.workspace.state_dir.rglob("*"))[:20]:
                    self.bench.observe(f"  {item.relative_to(self.workspace.state_dir)}")
            raise RuntimeError("Document UUID not found after sync")

        self.bench.ok(f"Uploaded document: {doc_uuid}")

        # Download fresh rm files from cloud to capture what we uploaded
        self._download_rm_files_to_cache(doc_uuid)

        # Capture phase 1 - the rm files we just uploaded
        self._capture_phase(
            phase_num=1,
            phase_name="post_upload",
            action="upload",
            prompt="Captured post-upload state (rm files we synced up)",
        )

        # Prompt user to wait for document to appear on device
        self.bench.prompt_user(
            "Document uploaded and syncing to your device...",
            "Please wait for the document to appear on your device.",
            "Then press Enter to continue...",
        )

        self._current_phase = 2

        return doc_uuid

    def wait_for_annotations(self, doc_uuid: str, timeout: float = 300.0) -> DocumentState:
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
                self.bench.observe("No annotations yet, waiting 5s before retry...")
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
            prompt=f"Annotations captured at phase {self._current_phase}",
        )

        self._current_phase += 1

        return self.get_document_state(doc_uuid)

    def trigger_sync(self) -> None:
        """Run sync command."""
        ret, out, err = self.workspace.run_sync("Sync")
        if ret != 0:
            raise RuntimeError(f"Sync failed: {err}")

    def capture_phase(self, phase_name: str, action: str = "capture") -> None:
        """Manually capture a phase at the current state.

        Use this after trigger_sync() or any operation to capture the current
        vault and device state as a named phase.

        Downloads fresh rm files from cloud to ensure we capture what was synced.

        Args:
            phase_name: Name for this phase (e.g., "post_modification")
            action: Action description for the phase metadata
        """
        if not self._current_test_id:
            raise RuntimeError("No test started - call start_test() first")

        # Download fresh rm files from cloud to capture current state
        doc_uuid = self.workspace.get_document_uuid()
        if doc_uuid:
            self._download_rm_files_to_cache(doc_uuid)

        self._capture_phase(
            phase_num=self._current_phase,
            phase_name=phase_name,
            action=action,
            prompt=f"Captured phase: {phase_name}",
        )
        self._current_phase += 1

    def get_document_state(self, doc_uuid: str) -> DocumentState:
        """Get current document state by downloading fresh from cloud.

        Downloads .rm files directly from reMarkable cloud to ensure we get
        the latest version after any sync operations that may have
        adjusted annotation positions.

        Args:
            doc_uuid: Document UUID

        Returns:
            Document state with fresh .rm files from cloud
        """
        import tempfile

        from rock_paper_sync.rm_cloud_client import RmCloudClient
        from rock_paper_sync.rm_cloud_sync import RmCloudSync

        rm_files: dict[str, bytes] = {}
        page_uuids: list[str] = []

        # Create cloud client and sync instance (cloud_url from workspace config)
        client = RmCloudClient(base_url=self.workspace.cloud_url)
        sync = RmCloudSync(base_url=self.workspace.cloud_url, client=client)

        # Get page UUIDs from cloud
        try:
            page_uuids = sync.get_existing_page_uuids(doc_uuid)
        except Exception as e:
            self.bench.warn(f"Failed to get page UUIDs: {e}")
            page_uuids = []

        if page_uuids:
            # Download fresh .rm files from cloud to temp directory
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                downloaded_files = sync.download_page_rm_files(doc_uuid, page_uuids, temp_path)
                for i, rm_path in enumerate(downloaded_files):
                    if rm_path and rm_path.exists():
                        page_uuid = page_uuids[i]
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

    def end_test(self, test_id: str) -> None:
        """Finalize test recording.

        Saves manifest and completes testdata capture.
        Assumes test succeeded (failed tests raise exceptions before reaching here).

        Args:
            test_id: Test identifier
        """
        if not self._current_test_id:
            return

        # Save manifest with all phases
        self._save_manifest()
        self.bench.ok(f"Recording complete: {test_id} " f"({len(self._phases)} phases)")

        self._current_test_id = None
        self._current_description = None
        self._current_phase = 0
        self._phases = []

    def observe_result(self, message: str = "") -> None:
        """Pause for user to observe result on device.

        Prompts user to view the synced content on their device and approve
        that it looks correct before proceeding to cleanup/unsync.

        Args:
            message: Optional message describing what to observe
        """
        default_msg = "Please observe the result on your device."
        observe_msg = message if message else default_msg

        self.bench.prompt_user(
            "📱 OBSERVE RESULT",
            observe_msg,
            "Verify the synced content looks correct on your device.",
            "Press Enter when you're ready to continue...",
        )

    def cleanup(self) -> None:
        """Cleanup after test with user confirmation.

        Prompts user to confirm that documents have been removed from their device.
        This is necessary in online mode because the user has a real device that
        needs time to sync the unsync operation.
        """
        self.bench.prompt_user(
            "Unsyncing from cloud (this may take a moment)...",
            "Please wait for the document to be removed from your device.",
            "Then press Enter to complete cleanup...",
        )

    def _download_rm_files_to_cache(self, doc_uuid: str) -> None:
        """Download rm files from cloud and save to cache directory.

        This ensures we can capture the rm files that were uploaded,
        even though the sync process doesn't save uploaded files to cache.

        Args:
            doc_uuid: Document UUID
        """
        from rock_paper_sync.rm_cloud_client import RmCloudClient
        from rock_paper_sync.rm_cloud_sync import RmCloudSync

        client = RmCloudClient(base_url=self.workspace.cloud_url)
        sync = RmCloudSync(base_url=self.workspace.cloud_url, client=client)

        try:
            page_uuids = sync.get_existing_page_uuids(doc_uuid)
            if page_uuids:
                # Create cache directory matching get_cached_rm_files() expectations
                cache_dir = self.workspace.cache_dir / "annotations" / doc_uuid
                cache_dir.mkdir(parents=True, exist_ok=True)

                # Download rm files directly to cache
                downloaded = sync.download_page_rm_files(doc_uuid, page_uuids, cache_dir)
                count = sum(1 for p in downloaded if p and p.exists())
                self.bench.observe(f"Downloaded {count} rm file(s) to cache")
        except Exception as e:
            self.bench.warn(f"Failed to download rm files to cache: {e}")

    def _capture_phase(
        self, phase_num: int, phase_name: str, action: str, prompt: str = ""
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

        test_dir = self.testdata_store.base_dir / self._current_test_id
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

        (phase_dir / "device_state.json").write_text(json.dumps(device_state, indent=2))

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

        (phase_dir / "phase_info.json").write_text(json.dumps(phase_info, indent=2))

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

        test_dir = self.testdata_store.base_dir / self._current_test_id

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
            "page_uuids": page_uuids,  # Required by TestManifest
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
                    "has_rm_files": bool(
                        p.device_state and p.device_state.get("rm_files_count", 0) > 0
                    ),
                }
                for p in self._phases
            ],
        }

        (test_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        self.bench.ok(f"Saved manifest: {test_dir / 'manifest.json'}")
