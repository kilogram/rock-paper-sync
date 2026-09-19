"""Integration tests for PDF push with rmfakecloud.

Validates the Phase 1 PDF push pipeline end-to-end:
(a) Push one markdown file, read back exactly 4 index entries, verify .pdf hash.
(b) Push unchanged file, assert no re-upload (index hash stable).
(c) Change font_size_pt, assert re-push (new pdf hash).
(d) Inject a fake .rm entry, assert InkNotPulledError; discard_ink=True succeeds.
"""

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from rock_paper_sync.config import (
    AppConfig,
    CloudConfig,
    LayoutConfig,
    OCRConfig,
    SyncConfig,
    VaultConfig,
)
from rock_paper_sync.converter import SyncEngine
from rock_paper_sync.rm_cloud_client import RmCloudClient
from rock_paper_sync.rm_cloud_sync import RmCloudSync
from rock_paper_sync.state import StateManager


def get_test_credentials() -> str:
    """Get device token from test credentials."""
    from tests.fixtures.rmfakecloud.helpers import get_credentials

    try:
        creds = get_credentials()
        return creds["device_token"]
    except FileNotFoundError as e:
        pytest.skip(f"rmfakecloud test credentials not found: {e}")


@pytest.fixture
def pdf_push_env(tmp_path: Path, rmfakecloud: str):
    """Create isolated test environment for PDF push integration tests."""
    vault = tmp_path / "vault"
    vault.mkdir()

    db = tmp_path / "state.db"
    log_file = tmp_path / "test.log"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    config = AppConfig(
        sync=SyncConfig(
            vaults=[
                VaultConfig(
                    name="test-vault",
                    path=vault,
                    remarkable_folder=None,  # Files go to root
                    include_patterns=["**/*.md"],
                    exclude_patterns=[],
                )
            ],
            state_database=db,
            debounce_seconds=1,
        ),
        cloud=CloudConfig(base_url=rmfakecloud),
        layout=LayoutConfig(),
        log_level="debug",
        log_file=log_file,
        ocr=OCRConfig(),
        cache_dir=cache_dir,
    )

    device_token = get_test_credentials()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "device_token": device_token,
                "device_id": "rock-paper-sync-test-001",
                "user_id": "",
            },
            f,
        )
        temp_creds_path = Path(f.name)

    rm_client = RmCloudClient(base_url=rmfakecloud, credentials_path=temp_creds_path)
    cloud_sync = RmCloudSync(base_url=rmfakecloud, client=rm_client)

    return {
        "config": config,
        "vault": vault,
        "cloud_sync": cloud_sync,
        "temp_creds_path": temp_creds_path,
    }


def _read_document_index(cloud_sync: RmCloudSync, doc_uuid: str) -> list:
    """Read the document index entries for a document via the sync client."""
    root_entries, _, _ = cloud_sync.get_root_state(use_cache=False)
    doc_entry = None
    for entry in root_entries:
        if entry.entry_name == doc_uuid:
            doc_entry = entry
            break
    if doc_entry is None:
        return []
    doc_index_content = cloud_sync.sync_client.download_blob(doc_entry.hash)
    return cloud_sync.sync_client.parse_index(doc_index_content)


def _get_doc_index_hash(cloud_sync: RmCloudSync, doc_uuid: str) -> str | None:
    """Get the document index hash from the root."""
    cloud_sync.invalidate_root_cache()
    return cloud_sync.get_document_index_hash(doc_uuid)


@pytest.mark.offline
class TestPdfPushIntegration:
    """PDF push integration tests against rmfakecloud."""

    def test_push_read_back_repush_config_change_ink_guard(self, pdf_push_env):
        """Full lifecycle: push, no-op, config change re-push, ink guard.

        Steps:
        (a) Push one file, verify 4 index entries and .pdf hash matches state.
        (b) Push unchanged, verify no re-upload.
        (c) Change font_size_pt, verify re-push with new pdf hash.
        (d) Inject .rm, verify InkNotPulledError; discard_ink=True succeeds.
        """
        config = pdf_push_env["config"]
        vault = pdf_push_env["vault"]
        cloud_sync = pdf_push_env["cloud_sync"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # Create test file
        test_file = vault / "test-pdf-push.md"
        test_file.write_text(
            "# PDF Push Test\n\n"
            "This document tests the PDF push pipeline.\n\n"
            "- Item one\n"
            "- Item two\n"
        )

        # --- (a) Initial push ---
        result = engine.sync_file(vault_config, test_file)
        assert result.success, f"Initial push failed: {result.error}"
        doc_uuid = result.remarkable_uuid
        assert doc_uuid is not None

        # Read back document index
        entries = _read_document_index(cloud_sync, doc_uuid)
        entry_names = {e.entry_name for e in entries}

        expected_names = {
            f"{doc_uuid}.metadata",
            f"{doc_uuid}.content",
            f"{doc_uuid}.pagedata",
            f"{doc_uuid}.pdf",
        }
        assert entry_names == expected_names, f"Expected exactly 4 PDF entries, got: {entry_names}"

        # Verify .pdf blob hash matches state
        file_state = state.get_file_state("test-vault", "test-pdf-push.md")
        assert file_state is not None
        assert file_state.status == "synced"
        assert file_state.pushed_pdf_sha256 is not None

        # Download the .pdf blob and verify its SHA-256
        pdf_entry = next(e for e in entries if e.entry_name.endswith(".pdf"))
        pdf_blob = cloud_sync.sync_client.download_blob(pdf_entry.hash)
        actual_pdf_sha256 = hashlib.sha256(pdf_blob).hexdigest()
        assert actual_pdf_sha256 == file_state.pushed_pdf_sha256, (
            f"PDF hash mismatch: state={file_state.pushed_pdf_sha256}, cloud={actual_pdf_sha256}"
        )

        initial_index_hash = _get_doc_index_hash(cloud_sync, doc_uuid)
        assert initial_index_hash is not None

        # --- (b) Push unchanged — no re-upload ---
        result2 = engine.sync_file(vault_config, test_file)
        assert result2.success
        assert result2.skipped, "Unchanged file should be skipped"

        # Index hash should be stable
        hash_after_noop = _get_doc_index_hash(cloud_sync, doc_uuid)
        assert hash_after_noop == initial_index_hash, "Index hash should not change on no-op push"

        # --- (c) Change font_size_pt — re-push ---
        from dataclasses import replace

        new_layout = replace(config.layout, font_size_pt=14.0)
        new_config = replace(config, layout=new_layout)

        state2 = StateManager(config.sync.state_database)
        engine2 = SyncEngine(new_config, state2, cloud_sync=cloud_sync)

        result3 = engine2.sync_file(vault_config, test_file)
        assert result3.success, f"Config-change re-push failed: {result3.error}"
        assert not result3.skipped, "Changed config should trigger re-push"

        # Index hash should have changed
        hash_after_config = _get_doc_index_hash(cloud_sync, doc_uuid)
        assert hash_after_config != initial_index_hash, (
            "Index hash should change after config re-push"
        )

        # New pdf hash should differ
        state_after = state2.get_file_state("test-vault", "test-pdf-push.md")
        assert state_after.pushed_pdf_sha256 != file_state.pushed_pdf_sha256, (
            "PDF hash should change after font_size_pt change"
        )

        # --- (d) Inject fake .rm, verify ink guard ---
        # Upload a fake .rm entry into the document's index
        fake_rm_content = b"fake rm file content for ink guard test"
        fake_rm_name = f"{doc_uuid}/fake-page-uuid.rm"

        # Get current files, add the fake .rm, re-upload
        current_entries = _read_document_index(cloud_sync, doc_uuid)
        current_files: dict[str, bytes] = {}
        for entry in current_entries:
            blob_data = cloud_sync.sync_client.download_blob(entry.hash)
            current_files[entry.entry_name] = blob_data
        current_files[fake_rm_name] = fake_rm_content

        # Re-upload with the injected .rm file
        cloud_sync.sync_client.upload_document(
            doc_uuid=doc_uuid,
            files=current_files,
            broadcast=False,
        )
        cloud_sync.invalidate_root_cache()

        # Verify the .rm entry is in the index
        entries_with_rm = _read_document_index(cloud_sync, doc_uuid)
        rm_entries = [e for e in entries_with_rm if e.entry_name.endswith(".rm")]
        assert len(rm_entries) == 1, f"Expected 1 .rm entry, got {len(rm_entries)}"

        # Modify content to force a push attempt
        test_file.write_text("# PDF Push Test (Modified)\n\nContent changed to trigger a push.\n")

        # Push should fail with InkNotPulledError
        state3 = StateManager(config.sync.state_database)
        engine3 = SyncEngine(config, state3, cloud_sync=cloud_sync)

        result4 = engine3.sync_file(vault_config, test_file)
        assert not result4.success, "Push should fail when ink is present"
        assert result4.error is not None
        assert "discard-ink" in result4.error.lower(), (
            f"Error should mention --discard-ink: {result4.error}"
        )

        # With discard_ink=True, push should succeed
        result5 = engine3.sync_file(vault_config, test_file, discard_ink=True)
        assert result5.success, f"discard_ink push failed: {result5.error}"

        # After forced push, the .rm entry should be gone (replaced by PDF-only payload)
        cloud_sync.invalidate_root_cache()
        entries_after_discard = _read_document_index(cloud_sync, doc_uuid)
        rm_after = [e for e in entries_after_discard if e.entry_name.endswith(".rm")]
        assert len(rm_after) == 0, (
            f"After discard_ink push, .rm entries should be gone, got {len(rm_after)}"
        )

        state.close()
        state2.close()
        state3.close()
