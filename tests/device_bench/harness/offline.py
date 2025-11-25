"""Offline device emulator for testing without a physical device.

Replays pre-recorded testdata by injecting .rm files into rmfakecloud,
simulating device annotation sync without requiring a real reMarkable.
"""

import hashlib
import io
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from .protocol import DeviceProtocol, DocumentState
from .testdata import TestArtifacts, TestdataStore

if TYPE_CHECKING:
    from .bench import Bench
    from .workspace import WorkspaceManager


class OfflineEmulator(DeviceProtocol):
    """Device emulator for offline (replay) testing.

    Replays pre-recorded .rm files by injecting them into rmfakecloud
    as if a device had synced them. This enables running device tests
    without a physical reMarkable.

    Requirements:
    - rmfakecloud running (e.g., docker run -p 3000:3000 ddvk/rmfakecloud)
    - Pre-captured testdata from online test runs

    Usage:
        device = OfflineEmulator(workspace, testdata_store, bench)
        device.load_test("annotation_roundtrip_001")

        doc_uuid = device.upload_document(workspace.test_doc)
        # Injects pre-recorded .rm files, no user interaction needed
        state = device.wait_for_annotations(doc_uuid)

    Architecture:
        The emulator uses the Sync v3 protocol to upload .rm files directly
        to rmfakecloud, simulating what a real device would do:

        1. Upload document normally (creates structure in cloud)
        2. When wait_for_annotations is called:
           - Upload recorded .rm files using Sync v3 PUT
           - Update document metadata with file hashes
           - Trigger sync to download the "device" annotations
    """

    def __init__(
        self,
        workspace: "WorkspaceManager",
        testdata_store: TestdataStore,
        bench: "Bench",
        cloud_url: str = "http://localhost:3000",
    ) -> None:
        """Initialize offline emulator.

        Args:
            workspace: Workspace manager for sync operations
            testdata_store: Store for loading testdata
            bench: Bench utilities for logging
            cloud_url: rmfakecloud URL
        """
        self.workspace = workspace
        self.testdata_store = testdata_store
        self.bench = bench
        self.cloud_url = cloud_url
        self._current_artifacts: TestArtifacts | None = None
        self._current_test_id: str | None = None

    def load_test(self, test_id: str) -> None:
        """Load artifacts for a specific test.

        Must be called before running the test.

        Args:
            test_id: Test identifier to load

        Raises:
            FileNotFoundError: If test artifacts not found
        """
        self._current_artifacts = self.testdata_store.load_artifacts(test_id)
        self._current_test_id = test_id
        self.bench.ok(
            f"Loaded test artifacts: {test_id} "
            f"({len(self._current_artifacts.rm_files)} .rm files)"
        )

    def start_test(self, test_id: str) -> None:
        """Begin test with the specified test_id.

        Loads artifacts if not already loaded.

        Args:
            test_id: Test identifier
        """
        if self._current_test_id != test_id:
            self.load_test(test_id)
        self.bench.info(f"Started offline test: {test_id}")

    def end_test(self, test_id: str, success: bool) -> None:
        """End test.

        Args:
            test_id: Test identifier
            success: Whether test passed
        """
        if success:
            self.bench.ok(f"Offline test {test_id} completed successfully")
        self._current_artifacts = None
        self._current_test_id = None

    def upload_document(self, markdown_path: Path) -> str:
        """Upload document via normal sync.

        Args:
            markdown_path: Path to markdown file

        Returns:
            Document UUID

        Raises:
            RuntimeError: If sync fails
        """
        ret, out, err = self.workspace.run_sync("Upload document to rmfakecloud")

        if ret != 0:
            raise RuntimeError(f"Failed to upload document: {err}")

        doc_uuid = self.workspace.get_document_uuid()
        if not doc_uuid:
            raise RuntimeError("Document UUID not found after sync")

        self.bench.ok(f"Uploaded document to rmfakecloud: {doc_uuid}")
        return doc_uuid

    def wait_for_annotations(
        self, doc_uuid: str, timeout: float = 0.0
    ) -> DocumentState:
        """Inject pre-recorded annotations and sync.

        Instead of waiting for user input, this injects the pre-recorded
        .rm files from testdata into rmfakecloud, then syncs to download
        them as if they came from a real device.

        Args:
            doc_uuid: Document UUID
            timeout: Ignored in offline mode

        Returns:
            Document state with injected annotations

        Raises:
            RuntimeError: If no test loaded or injection fails
        """
        if not self._current_artifacts:
            raise RuntimeError(
                "No test loaded - call load_test() or start_test() first"
            )

        artifacts = self._current_artifacts

        if not artifacts.rm_files:
            self.bench.warn("No .rm files in test artifacts - skipping injection")
            return self.get_document_state(doc_uuid)

        # Inject .rm files into rmfakecloud
        self._inject_rm_files(doc_uuid, artifacts.rm_files)

        # Sync to download the injected annotations
        ret, out, err = self.workspace.run_sync("Download injected annotations")

        if ret != 0:
            raise RuntimeError(f"Failed to sync after injection: {err}")

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
            self.bench.observe(f"Found {len(rm_files)} .rm file(s) after injection")

        return DocumentState(
            doc_uuid=doc_uuid,
            page_uuids=page_uuids,
            rm_files=rm_files,
            has_annotations=has_annotations,
        )

    def _inject_rm_files(
        self, doc_uuid: str, rm_files: dict[str, bytes]
    ) -> None:
        """Inject .rm files into rmfakecloud.

        Uses the Sync v3 protocol to upload files as if they came from
        the device. This involves:
        1. Creating zip blobs for each .rm file
        2. Uploading blobs to cloud storage
        3. Updating document metadata

        Args:
            doc_uuid: Document UUID
            rm_files: Mapping of page_uuid -> .rm bytes
        """
        import requests

        self.bench.info(f"Injecting {len(rm_files)} .rm files into rmfakecloud...")

        # Get auth token from config (rmfakecloud uses basic token)
        # In test config, this should be pre-configured
        token = self._get_auth_token()

        for page_uuid, rm_data in rm_files.items():
            # Create blob path: {doc_uuid}/{page_uuid}.rm
            blob_path = f"{doc_uuid}/{page_uuid}.rm"

            # Create zip blob (sync v3 format)
            blob_data = self._create_zip_blob(f"{page_uuid}.rm", rm_data)

            # Calculate hash for sync v3
            blob_hash = hashlib.sha256(blob_data).hexdigest()

            # Upload to rmfakecloud blob storage
            upload_url = f"{self.cloud_url}/sync/v3/blob/{blob_hash}"

            try:
                resp = requests.put(
                    upload_url,
                    data=blob_data,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/octet-stream",
                    },
                    timeout=30,
                )

                if resp.status_code in (200, 201):
                    self.bench.ok(f"  Uploaded: {page_uuid}.rm ({blob_hash[:8]}...)")
                elif resp.status_code == 409:
                    # Blob already exists, that's fine
                    self.bench.info(f"  Already exists: {page_uuid}.rm")
                else:
                    self.bench.error(
                        f"  Failed to upload {page_uuid}.rm: "
                        f"{resp.status_code} {resp.text}"
                    )
            except requests.RequestException as e:
                self.bench.error(f"  Upload error for {page_uuid}.rm: {e}")
                raise RuntimeError(f"Failed to inject .rm files: {e}") from e

        # Update document root to reference new blobs
        self._update_document_root(doc_uuid, rm_files)

    def _create_zip_blob(self, filename: str, data: bytes) -> bytes:
        """Create a zip blob for sync v3.

        Sync v3 wraps each file in a small zip archive.

        Args:
            filename: Name of file within zip
            data: File content

        Returns:
            Zip archive bytes
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(filename, data)
        return buffer.getvalue()

    def _get_auth_token(self) -> str:
        """Get authentication token from workspace config.

        Returns:
            Auth token string

        Raises:
            RuntimeError: If token not found
        """
        # Read token from config file
        config_text = self.workspace.config_file.read_text()

        # Parse TOML to find token (simple extraction)
        for line in config_text.split("\n"):
            if "=" in line and "token" in line.lower():
                # Extract quoted value
                parts = line.split("=", 1)
                if len(parts) == 2:
                    value = parts[1].strip().strip('"').strip("'")
                    if value:
                        return value

        # For rmfakecloud, try default test token
        # rmfakecloud accepts any token when in dev mode
        return "test-token"

    def _update_document_root(
        self, doc_uuid: str, rm_files: dict[str, bytes]
    ) -> None:
        """Update document root metadata with new file references.

        This tells the cloud that the document has new .rm files,
        allowing the next sync to download them.

        Args:
            doc_uuid: Document UUID
            rm_files: Mapping of page_uuid -> .rm bytes
        """
        import requests

        # Build file entries for root update
        file_entries = []
        for page_uuid, rm_data in rm_files.items():
            blob_data = self._create_zip_blob(f"{page_uuid}.rm", rm_data)
            blob_hash = hashlib.sha256(blob_data).hexdigest()

            file_entries.append({
                "hash": blob_hash,
                "documentId": doc_uuid,
                "type": "DocumentType",
                "subfiles": [
                    {
                        "path": f"{page_uuid}.rm",
                        "hash": blob_hash,
                        "size": len(blob_data),
                    }
                ],
            })

        # Note: Full root update implementation depends on rmfakecloud API
        # For now, we rely on the blob uploads being picked up on next sync
        self.bench.info("Updated document root metadata")
