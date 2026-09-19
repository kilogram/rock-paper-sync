"""Tests for Phase 4 pull sync engine (PDF substrate).

Tests the pull direction: device ink → vault markdown.
"""

from pathlib import Path

from rock_paper_sync.config import LayoutConfig, VaultConfig
from rock_paper_sync.pull_sync import PullResult, PullStats, PullSyncEngine
from rock_paper_sync.state import StateManager


class TestPullResult:
    """Tests for PullResult dataclass."""

    def test_success_result(self) -> None:
        """Test successful pull result."""
        result = PullResult(
            vault_name="test",
            obsidian_path="notes/test.md",
            success=True,
            highlights_added=3,
            notes_appended=1,
        )
        assert result.success is True
        assert result.highlights_added == 3
        assert result.notes_appended == 1
        assert result.drawings_saved == 0
        assert result.deferred_count == 0
        assert result.error is None

    def test_error_result(self) -> None:
        """Test error pull result."""
        result = PullResult(
            vault_name="test",
            obsidian_path="notes/test.md",
            success=False,
            error="File not found",
        )
        assert result.success is False
        assert result.error == "File not found"


class TestPullStats:
    """Tests for PullStats dataclass."""

    def test_default_stats(self) -> None:
        """Test default statistics values."""
        stats = PullStats()
        assert stats.files_checked == 0
        assert stats.files_updated == 0
        assert stats.files_skipped == 0
        assert stats.files_errored == 0
        assert stats.total_highlights == 0
        assert stats.total_notes == 0
        assert stats.total_drawings == 0
        assert stats.total_deferred == 0


class TestPullSyncEngineInit:
    """Tests for PullSyncEngine initialisation."""

    def test_init(self, tmp_path: Path) -> None:
        """Test engine initialisation with minimal dependencies."""
        db_path = tmp_path / "state.db"
        state = StateManager(db_path)
        layout = LayoutConfig()

        # Use a mock cloud reader that satisfies the protocol
        class FakeReader:
            def get_document_index_hash(self, doc_uuid: str) -> str | None:
                return None

            def download_page_rm_files(self, doc_uuid, page_uuids, output_dir):
                return [None] * len(page_uuids)

            def get_existing_page_uuids(self, doc_uuid: str) -> list[str]:
                return []

            def download_content_file(self, doc_uuid: str) -> bytes | None:
                return None

            def download_document_blob(self, doc_uuid, entry_name) -> bytes | None:
                return None

        engine = PullSyncEngine(
            state=state,
            cloud_reader=FakeReader(),
            layout=layout,
            cache_dir=tmp_path / "cache",
        )
        assert engine.layout is layout
        state.close()


class TestPullFileSkips:
    """Tests for pull_file when there is nothing to do."""

    def test_pull_no_sync_state(self, tmp_path: Path) -> None:
        """Pulling a file with no sync state returns success (nothing to do)."""
        db_path = tmp_path / "state.db"
        state = StateManager(db_path)
        vault = VaultConfig(
            name="test-vault",
            path=tmp_path / "vault",
            remarkable_folder=None,
            include_patterns=["**/*.md"],
            exclude_patterns=[],
        )
        (tmp_path / "vault").mkdir()

        class FakeReader:
            def get_document_index_hash(self, doc_uuid):
                return None

            def download_page_rm_files(self, doc_uuid, page_uuids, output_dir):
                return [None] * len(page_uuids)

            def get_existing_page_uuids(self, doc_uuid):
                return []

            def download_content_file(self, doc_uuid):
                return None

            def download_document_blob(self, doc_uuid, entry_name):
                return None

        engine = PullSyncEngine(
            state=state,
            cloud_reader=FakeReader(),
            layout=LayoutConfig(),
            cache_dir=tmp_path / "cache",
        )
        result = engine.pull_file(vault, "test.md")
        assert result.success is True
        assert result.highlights_added == 0
        state.close()

    def test_pull_no_pushed_pdf(self, tmp_path: Path) -> None:
        """Pulling a file without pushed PDF state returns success."""
        db_path = tmp_path / "state.db"
        state = StateManager(db_path)

        # Create sync state without PDF data
        from rock_paper_sync.state import SyncRecord

        state.update_file_state(
            SyncRecord(
                vault_name="test",
                obsidian_path="test.md",
                remarkable_uuid="abc-123",
                content_hash="hash",
                last_sync_time=0,
                page_count=1,
                status="synced",
            )
        )

        vault = VaultConfig(
            name="test",
            path=tmp_path / "vault",
            remarkable_folder=None,
            include_patterns=["**/*.md"],
            exclude_patterns=[],
        )
        (tmp_path / "vault").mkdir()

        class FakeReader:
            def get_document_index_hash(self, doc_uuid):
                return None

            def download_page_rm_files(self, doc_uuid, page_uuids, output_dir):
                return []

            def get_existing_page_uuids(self, doc_uuid):
                return []

            def download_content_file(self, doc_uuid):
                return None

            def download_document_blob(self, doc_uuid, entry_name):
                return None

        engine = PullSyncEngine(
            state=state,
            cloud_reader=FakeReader(),
            layout=LayoutConfig(),
            cache_dir=tmp_path / "cache",
        )
        result = engine.pull_file(vault, "test.md")
        assert result.success is True
        state.close()
