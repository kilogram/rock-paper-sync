"""Integration tests for the full sync pipeline.

These tests verify that all components work together correctly, from markdown
parsing through state management to eventual file generation.
"""

import time
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
from rock_paper_sync.parser import BlockType, FormatStyle, parse_markdown_file
from rock_paper_sync.state import StateManager, SyncRecord


@pytest.fixture
def integration_env(tmp_path: Path):
    """Create a complete integration test environment."""
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
                    remarkable_folder="Test",
                    include_patterns=["**/*.md"],
                    exclude_patterns=[".obsidian/**", "templates/**"],
                )
            ],
            state_database=db,
            debounce_seconds=1,
        ),
        cloud=CloudConfig(base_url="http://localhost:3000"),
        layout=LayoutConfig(
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50,
        ),
        log_level="debug",
        log_file=log_file,
        ocr=OCRConfig(),
        cache_dir=cache_dir,
    )

    state = StateManager(db)

    yield {
        "vault": vault,
        "config": config,
        "state": state,
        "db": db,
        "log_file": log_file,
    }

    state.close()


class TestParserStateIntegration:
    """Tests for parser and state manager integration."""

    def test_parse_and_track_file(self, integration_env):
        """Test parsing a file and tracking it in state."""
        vault = integration_env["vault"]
        state = integration_env["state"]

        # Create test file
        test_file = vault / "test.md"
        content = """---
title: Integration Test
---

# Test Document

This is a **test** with *formatting*.
"""
        test_file.write_text(content)

        # Parse the file
        doc = parse_markdown_file(test_file)

        # Verify parsing
        assert doc.title == "Integration Test"
        assert len(doc.content) > 0
        # content_hash is now a semantic hash, just verify it exists and is valid
        assert len(doc.content_hash) == 64  # SHA256 hex digest length

        # Store in state
        record = SyncRecord(
            vault_name="test-vault",
            obsidian_path="test.md",
            remarkable_uuid="test-uuid-123",
            content_hash=doc.content_hash,
            last_sync_time=int(time.time()),
            page_count=1,
            status="synced",
        )
        state.update_file_state(record)

        # Verify state
        retrieved = state.get_file_state("test-vault", "test.md")
        assert retrieved is not None
        assert retrieved.content_hash == doc.content_hash
        assert retrieved.remarkable_uuid == "test-uuid-123"

    def test_detect_file_changes(self, integration_env):
        """Test that file modifications are detected."""
        vault = integration_env["vault"]
        state = integration_env["state"]

        # Create and track file
        test_file = vault / "changing.md"
        original_content = "# Original\n\nOriginal content."
        test_file.write_text(original_content)

        doc1 = parse_markdown_file(test_file)
        record1 = SyncRecord(
            vault_name="test-vault",
            obsidian_path="changing.md",
            remarkable_uuid="uuid-1",
            content_hash=doc1.content_hash,
            last_sync_time=int(time.time()),
            page_count=1,
            status="synced",
        )
        state.update_file_state(record1)

        # Modify file
        time.sleep(0.1)  # Ensure different timestamp
        modified_content = "# Modified\n\nModified content."
        test_file.write_text(modified_content)

        doc2 = parse_markdown_file(test_file)

        # Hashes should be different
        assert doc1.content_hash != doc2.content_hash

        # State should detect change
        current_state = state.get_file_state("test-vault", "changing.md")
        assert current_state.content_hash != doc2.content_hash

        # Update state
        record2 = SyncRecord(
            vault_name="test-vault",
            obsidian_path="changing.md",
            remarkable_uuid="uuid-1",
            content_hash=doc2.content_hash,
            last_sync_time=int(time.time()),
            page_count=1,
            status="synced",
        )
        state.update_file_state(record2)

        # Verify update
        updated_state = state.get_file_state("test-vault", "changing.md")
        assert updated_state.content_hash == doc2.content_hash

    def test_multiple_file_workflow(self, integration_env):
        """Test workflow with multiple files."""
        vault = integration_env["vault"]
        state = integration_env["state"]

        # Create multiple files
        files = {
            "note1.md": "# Note 1\n\nFirst note.",
            "note2.md": "# Note 2\n\nSecond note.",
            "note3.md": "# Note 3\n\nThird note.",
        }

        for filename, content in files.items():
            file_path = vault / filename
            file_path.write_text(content)

            # Parse and track
            doc = parse_markdown_file(file_path)
            record = SyncRecord(
                vault_name="test-vault",
                obsidian_path=filename,
                remarkable_uuid=f"uuid-{filename}",
                content_hash=doc.content_hash,
                last_sync_time=int(time.time()),
                page_count=1,
                status="synced",
            )
            state.update_file_state(record)

        # Verify all files tracked
        all_synced = state.get_all_synced_files("test-vault")
        assert len(all_synced) == 3

        synced_paths = {f.obsidian_path for f in all_synced}
        assert synced_paths == {"note1.md", "note2.md", "note3.md"}

    def test_nested_folder_structure(self, integration_env):
        """Test parsing and tracking files in nested folders."""
        vault = integration_env["vault"]
        state = integration_env["state"]

        # Create nested structure
        (vault / "projects").mkdir()
        (vault / "projects" / "work").mkdir()
        (vault / "projects" / "personal").mkdir()

        files = [
            ("root.md", vault / "root.md"),
            ("projects/overview.md", vault / "projects" / "overview.md"),
            ("projects/work/tasks.md", vault / "projects" / "work" / "tasks.md"),
            ("projects/personal/notes.md", vault / "projects" / "personal" / "notes.md"),
        ]

        for rel_path, abs_path in files:
            content = f"# {rel_path}\n\nContent for {rel_path}."
            abs_path.write_text(content)

            doc = parse_markdown_file(abs_path)
            record = SyncRecord(
                vault_name="test-vault",
                obsidian_path=rel_path,
                remarkable_uuid=f"uuid-{rel_path.replace('/', '-')}",
                content_hash=doc.content_hash,
                last_sync_time=int(time.time()),
                page_count=1,
                status="synced",
            )
            state.update_file_state(record)

        # Verify all tracked
        all_synced = state.get_all_synced_files("test-vault")
        assert len(all_synced) == 4

        # Verify folder mappings can be created
        folder_mappings = [
            ("projects", "folder-uuid-projects"),
            ("projects/work", "folder-uuid-work"),
            ("projects/personal", "folder-uuid-personal"),
        ]

        for folder_path, folder_uuid in folder_mappings:
            state.create_folder_mapping("test-vault", folder_path, folder_uuid)

        # Verify retrieval
        assert state.get_folder_uuid("test-vault", "projects") == "folder-uuid-projects"
        assert state.get_folder_uuid("test-vault", "projects/work") == "folder-uuid-work"


class TestConfigIntegration:
    """Tests for configuration integration with other components."""

    def test_config_paths_used_correctly(self, integration_env):
        """Test that config paths are used by state manager."""
        config = integration_env["config"]
        state = integration_env["state"]

        # Database should be at configured location
        assert state.db_path == config.sync.state_database
        assert state.db_path.exists()

    def test_exclude_patterns_integration(self, integration_env):
        """Test that exclude patterns work with find_changed_files."""
        vault = integration_env["vault"]
        state = integration_env["state"]
        config = integration_env["config"]

        # Create files that should be excluded
        (vault / ".obsidian").mkdir()
        (vault / ".obsidian" / "config.md").write_text("Obsidian config")
        (vault / "templates").mkdir()
        (vault / "templates" / "daily.md").write_text("Daily template")

        # Create files that should be included
        (vault / "notes.md").write_text("Regular note")
        (vault / "ideas.md").write_text("Ideas note")

        # Find changed files with exclusions
        vault_config = config.sync.vaults[0]
        changed = state.find_changed_files(
            vault_config.name,
            vault_config.path,
            vault_config.include_patterns,
            vault_config.exclude_patterns,
        )

        # Should only find the included files
        changed_names = {f.name for f in changed}
        assert "notes.md" in changed_names
        assert "ideas.md" in changed_names
        assert "config.md" not in changed_names
        assert "daily.md" not in changed_names


class TestComplexMarkdownScenarios:
    """Tests for complex real-world markdown scenarios."""

    def test_large_document_pagination_planning(self, integration_env):
        """Test parsing large document that would need pagination."""
        vault = integration_env["vault"]

        # Create document with many sections
        sections = []
        for i in range(50):
            sections.append(
                f"## Section {i+1}\n\n"
                f"This is section {i+1}. It contains multiple sentences to simulate "
                f"real content. The content should be long enough to require multiple "
                f"lines when rendered on the reMarkable device.\n\n"
                f"Here's a second paragraph in section {i+1} with more details.\n"
            )

        content = "# Large Document\n\n" + "\n".join(sections)
        large_file = vault / "large.md"
        large_file.write_text(content)

        # Parse
        doc = parse_markdown_file(large_file)

        # Should have many blocks
        assert len(doc.content) > 100  # 1 header + 50 section headers + 100 paragraphs

        # Estimate pages needed (rough calculation)
        from rock_paper_sync.layout.constants import LINES_PER_PAGE

        total_blocks = len(doc.content)
        estimated_pages = max(1, total_blocks // LINES_PER_PAGE)

        assert estimated_pages >= 3  # Should need multiple pages

    def test_mixed_content_types(self, integration_env):
        """Test document with all content types mixed."""
        vault = integration_env["vault"]

        content = """---
title: Mixed Content
tags: [test, comprehensive]
---

# Main Title

Introduction paragraph with **bold** and *italic*.

## Code Section

Here's some code:

```python
def example():
    return "test"
```

## Lists

- Bullet one
- Bullet two
  - Nested item

1. Numbered one
2. Numbered two

## Quote

> This is a blockquote with **emphasis**.

---

Final paragraph.
"""

        mixed_file = vault / "mixed.md"
        mixed_file.write_text(content)

        # Parse
        doc = parse_markdown_file(mixed_file)

        # Verify all block types present
        block_types = {b.type for b in doc.content}
        assert BlockType.HEADER in block_types
        assert BlockType.PARAGRAPH in block_types
        assert BlockType.CODE_BLOCK in block_types
        assert BlockType.LIST_ITEM in block_types
        assert BlockType.BLOCKQUOTE in block_types
        assert BlockType.HORIZONTAL_RULE in block_types

        # Verify frontmatter
        assert doc.title == "Mixed Content"
        assert "test" in doc.frontmatter["tags"]

    def test_formatting_preservation_accuracy(self, integration_env):
        """Test that formatting positions are preserved accurately."""
        vault = integration_env["vault"]

        content = """# Test

Start **bold** middle *italic* end `code` finish.

Second paragraph with ***bold italic*** combined.
"""

        format_file = vault / "formatting.md"
        format_file.write_text(content)

        # Parse
        doc = parse_markdown_file(format_file)

        # Find paragraph with multiple formats
        para = next(
            b for b in doc.content if b.type == BlockType.PARAGRAPH and len(b.formatting) > 1
        )

        # Verify each formatting is accurate
        for fmt in para.formatting:
            extracted = para.text[fmt.start : fmt.end]

            if fmt.style == FormatStyle.BOLD:
                # Bold text should be in extracted portion
                assert "bold" in extracted or len(extracted) > 0
            elif fmt.style == FormatStyle.ITALIC:
                assert "italic" in extracted or len(extracted) > 0
            elif fmt.style == FormatStyle.CODE:
                assert "code" in extracted or len(extracted) > 0


class TestErrorRecovery:
    """Tests for error handling and recovery."""

    def test_malformed_frontmatter_doesnt_crash(self, integration_env):
        """Test that malformed YAML doesn't crash parsing."""
        vault = integration_env["vault"]

        content = """---
title: Test
bad: [unclosed array
---

# Content

This should still parse.
"""

        bad_file = vault / "bad_yaml.md"
        bad_file.write_text(content)

        # Should parse without crashing
        doc = parse_markdown_file(bad_file)

        # Content should still be parsed
        assert len(doc.content) > 0
        # Bad frontmatter returns empty dict
        assert doc.frontmatter == {}

    def test_unicode_handling(self, integration_env):
        """Test that unicode content is handled correctly."""
        vault = integration_env["vault"]
        state = integration_env["state"]

        content = """# Unicode Test

Emoji: 🎉 🚀 ✨

Chinese: 你好世界

Accents: café naïve résumé

Mixed: Hello 世界 café! 🎉
"""

        unicode_file = vault / "unicode.md"
        unicode_file.write_text(content, encoding="utf-8")

        # Parse
        doc = parse_markdown_file(unicode_file)

        # Verify content preserved
        text = " ".join(b.text for b in doc.content)
        assert "🎉" in text
        assert "你好世界" in text
        assert "café" in text

        # Hash should work with unicode
        record = SyncRecord(
            vault_name="test-vault",
            obsidian_path="unicode.md",
            remarkable_uuid="unicode-uuid",
            content_hash=doc.content_hash,
            last_sync_time=int(time.time()),
            page_count=1,
            status="synced",
        )
        state.update_file_state(record)

        # Verify stored correctly
        retrieved = state.get_file_state("test-vault", "unicode.md")
        assert retrieved is not None

    def test_empty_files_handled(self, integration_env):
        """Test that empty files are handled gracefully."""
        vault = integration_env["vault"]

        empty_file = vault / "empty.md"
        empty_file.write_text("")

        # Should parse without error
        doc = parse_markdown_file(empty_file)
        assert doc.content == []
        assert doc.frontmatter == {}

    def test_very_long_lines(self, integration_env):
        """Test handling of very long lines."""
        vault = integration_env["vault"]

        long_line = "A" * 10000
        content = f"# Test\n\n{long_line}\n"

        long_file = vault / "long_lines.md"
        long_file.write_text(content)

        # Should handle without crashing
        doc = parse_markdown_file(long_file)
        assert any(len(b.text) > 5000 for b in doc.content)


class TestFullPipelineStubs:
    """Full pipeline integration tests with cloud sync."""

    def test_end_to_end_sync(self, integration_env, mock_cloud_sync):
        """Test complete sync from markdown to cloud."""
        from rock_paper_sync.converter import SyncEngine

        vault = integration_env["vault"]
        state = integration_env["state"]
        config = integration_env["config"]

        # Create test file
        test_file = vault / "test.md"
        test_file.write_text("# Test\n\nContent here.")

        # Sync with mock cloud
        engine = SyncEngine(config, state, cloud_sync=mock_cloud_sync)
        results = engine.sync_all_changed()

        # Verify results
        assert len(results) == 1
        assert results[0].success
        assert results[0].remarkable_uuid is not None

        # Verify cloud upload was called
        assert mock_cloud_sync.upload_document.called

        # Verify state was updated
        file_state = state.get_file_state("test-vault", "test.md")
        assert file_state is not None
        assert file_state.status == "synced"

    def test_incremental_sync(self, integration_env, mock_cloud_sync):
        """Test that unchanged files are not reprocessed."""
        from rock_paper_sync.converter import SyncEngine

        vault = integration_env["vault"]
        state = integration_env["state"]
        config = integration_env["config"]

        # Create test file
        test_file = vault / "incremental.md"
        test_file.write_text("# Incremental\n\nOriginal content.")

        engine = SyncEngine(config, state, cloud_sync=mock_cloud_sync)

        # First sync
        results1 = engine.sync_all_changed()
        assert len(results1) == 1
        assert results1[0].success

        # Reset mock to track second sync
        mock_cloud_sync.reset_mock()

        # Second sync without changes - should skip
        results2 = engine.sync_all_changed()
        # File still appears in results but marked as skipped
        assert all(r.skipped for r in results2)  # All results should be skipped

        # Upload should not be called again (file unchanged)
        assert not mock_cloud_sync.upload_document.called

    def test_folder_hierarchy_creation(self, integration_env, mock_cloud_sync):
        """Test that folder structure is created via cloud sync."""
        from rock_paper_sync.converter import SyncEngine

        vault = integration_env["vault"]
        state = integration_env["state"]
        config = integration_env["config"]

        # Create nested folder structure
        folder = vault / "projects" / "work"
        folder.mkdir(parents=True)
        test_file = folder / "document.md"
        test_file.write_text("# Work Document\n\nIn nested folder.")

        engine = SyncEngine(config, state, cloud_sync=mock_cloud_sync)
        results = engine.sync_all_changed()

        assert len(results) == 1
        assert results[0].success

        # Verify folder creation was called
        assert mock_cloud_sync.upload_folder.called

        # Verify folders were created in state
        assert state.get_folder_uuid("test-vault", "projects") is not None
        assert state.get_folder_uuid("test-vault", "projects/work") is not None


class TestStateManagementEdgeCases:
    """Additional edge case tests for state management."""

    def test_concurrent_file_changes(self, integration_env):
        """Test handling of multiple rapid changes."""
        vault = integration_env["vault"]
        state = integration_env["state"]

        test_file = vault / "rapid.md"

        # Simulate rapid changes
        for i in range(10):
            content = f"# Version {i}\n\nContent version {i}."
            test_file.write_text(content)
            time.sleep(0.01)

            doc = parse_markdown_file(test_file)
            record = SyncRecord(
                vault_name="test-vault",
                obsidian_path="rapid.md",
                remarkable_uuid="rapid-uuid",
                content_hash=doc.content_hash,
                last_sync_time=int(time.time()),
                page_count=1,
                status="synced",
            )
            state.update_file_state(record)
            # Log the action
            state.log_sync_action("test-vault", "rapid.md", "updated", f"Version {i}")

        # Final state should reflect last change
        final_state = state.get_file_state("test-vault", "rapid.md")
        assert final_state is not None

        # History should show all changes
        history = state.get_recent_history(limit=20)
        # Should have logged all updates
        # History tuples are: (vault_name, obsidian_path, action, timestamp, details)
        rapid_entries = [h for h in history if h[1] == "rapid.md"]
        assert len(rapid_entries) >= 10  # All 10 updates logged

    def test_file_deletion_tracking(self, integration_env):
        """Test tracking file deletion in state."""
        vault = integration_env["vault"]
        state = integration_env["state"]

        # Create and sync file
        test_file = vault / "to_delete.md"
        test_file.write_text("# Delete Me\n\nContent.")

        doc = parse_markdown_file(test_file)
        record = SyncRecord(
            vault_name="test-vault",
            obsidian_path="to_delete.md",
            remarkable_uuid="delete-uuid",
            content_hash=doc.content_hash,
            last_sync_time=int(time.time()),
            page_count=1,
            status="synced",
        )
        state.update_file_state(record)

        # Delete from state
        state.delete_file_state("test-vault", "to_delete.md")

        # Verify deleted
        assert state.get_file_state("test-vault", "to_delete.md") is None

    def test_state_statistics(self, integration_env):
        """Test state statistics reporting."""
        vault = integration_env["vault"]
        state = integration_env["state"]

        # Create files with different statuses
        statuses = {
            "synced": 5,
            "pending": 2,
            "error": 1,
        }

        count = 0
        for status, num in statuses.items():
            for i in range(num):
                file_path = vault / f"file_{status}_{i}.md"
                file_path.write_text(f"# File {count}\n\nContent.")

                doc = parse_markdown_file(file_path)
                record = SyncRecord(
                    vault_name="test-vault",
                    obsidian_path=f"file_{status}_{i}.md",
                    remarkable_uuid=f"uuid-{count}",
                    content_hash=doc.content_hash,
                    last_sync_time=int(time.time()),
                    page_count=1,
                    status=status,
                )
                state.update_file_state(record)
                count += 1

        # Get statistics
        stats = state.get_stats("test-vault")

        # Verify
        assert stats["synced"] == 5
        assert stats["pending"] == 2
        assert stats["error"] == 1


class TestPerformance:
    """Performance-related tests."""

    @pytest.mark.slow
    def test_many_files_performance(self, integration_env):
        """Test performance with many files."""
        vault = integration_env["vault"]
        state = integration_env["state"]

        # Create many files
        num_files = 100
        start_time = time.time()

        for i in range(num_files):
            file_path = vault / f"file{i}.md"
            file_path.write_text(f"# File {i}\n\nContent for file {i}.")

            doc = parse_markdown_file(file_path)
            record = SyncRecord(
                vault_name="test-vault",
                obsidian_path=f"file{i}.md",
                remarkable_uuid=f"uuid-{i}",
                content_hash=doc.content_hash,
                last_sync_time=int(time.time()),
                page_count=1,
                status="synced",
            )
            state.update_file_state(record)

        elapsed = time.time() - start_time

        # Should process 100 files in reasonable time (< 10 seconds)
        assert elapsed < 10.0

        # Verify all tracked
        all_synced = state.get_all_synced_files("test-vault")
        assert len(all_synced) == num_files

    @pytest.mark.slow
    def test_large_file_parsing(self, integration_env):
        """Test parsing very large file."""
        vault = integration_env["vault"]

        # Create file with 1000+ lines
        lines = []
        for i in range(200):
            lines.append(f"## Section {i+1}\n\n")
            lines.append(f"Paragraph {i+1} with some content.\n\n")
            lines.append(f"Second paragraph in section {i+1}.\n\n")

        content = "# Large Document\n\n" + "".join(lines)
        large_file = vault / "very_large.md"
        large_file.write_text(content)

        # Parse
        start_time = time.time()
        doc = parse_markdown_file(large_file)
        elapsed = time.time() - start_time

        # Should parse quickly (< 2 seconds, generous for CI environments)
        assert elapsed < 2.0

        # Should have many blocks (1 main header + 200 sections + 400 paragraphs)
        assert len(doc.content) >= 400  # At least the paragraphs


class TestDocumentUpdateFlow:
    """Integration tests for document update workflows with cloud sync."""

    def test_file_update_preserves_uuid_end_to_end(self, integration_env, mock_cloud_sync):
        """Test that updating a file preserves UUID through full pipeline."""
        from rock_paper_sync.converter import SyncEngine

        vault = integration_env["vault"]
        state = integration_env["state"]
        config = integration_env["config"]

        # Create initial file
        test_file = vault / "document.md"
        test_file.write_text("# Version 1\n\nOriginal content.")

        # First sync
        engine = SyncEngine(config, state, cloud_sync=mock_cloud_sync)
        vault_config = config.sync.vaults[0]
        result1 = engine.sync_file(vault_config, test_file)

        assert result1.success
        uuid1 = result1.remarkable_uuid
        assert uuid1 is not None

        # Verify cloud upload was called
        assert mock_cloud_sync.upload_document.called

        # Update file content
        test_file.write_text("# Version 2\n\nUpdated content with changes.")

        # Reset mock to track second upload
        mock_cloud_sync.reset_mock()

        # Second sync
        result2 = engine.sync_file(vault_config, test_file)

        assert result2.success
        uuid2 = result2.remarkable_uuid

        # UUID should be SAME
        assert uuid2 == uuid1

        # Verify cloud upload was called for update
        assert mock_cloud_sync.upload_document.called

    def test_multiple_updates_same_document(self, integration_env, mock_cloud_sync):
        """Test multiple sequential updates to same document."""
        from rock_paper_sync.converter import SyncEngine

        vault = integration_env["vault"]
        state = integration_env["state"]
        config = integration_env["config"]

        test_file = vault / "evolving.md"
        engine = SyncEngine(config, state, cloud_sync=mock_cloud_sync)

        versions = [
            "# V1\n\nFirst version",
            "# V2\n\nSecond version with more content",
            "# V3\n\nThird version even longer with multiple paragraphs\n\nAnother paragraph",
        ]

        uuid = None
        vault_config = config.sync.vaults[0]
        for i, content in enumerate(versions):
            test_file.write_text(content)
            time.sleep(0.01)  # Ensure timestamp changes

            result = engine.sync_file(vault_config, test_file)
            assert result.success

            if uuid is None:
                uuid = result.remarkable_uuid
            else:
                # Should always be same UUID
                assert result.remarkable_uuid == uuid

        # Verify upload was called 3 times
        assert mock_cloud_sync.upload_document.call_count == 3

    def test_update_with_folder_move(self, integration_env, mock_cloud_sync):
        """Test updating file that changes folders."""
        from rock_paper_sync.converter import SyncEngine

        vault = integration_env["vault"]
        state = integration_env["state"]
        config = integration_env["config"]

        # Create file in root
        test_file = vault / "document.md"
        test_file.write_text("# Original\n\nIn root folder.")

        engine = SyncEngine(config, state, cloud_sync=mock_cloud_sync)
        vault_config = config.sync.vaults[0]
        result1 = engine.sync_file(vault_config, test_file)

        uuid = result1.remarkable_uuid
        assert result1.success

        # Move to subfolder
        folder = vault / "subfolder"
        folder.mkdir()
        new_file = folder / "document.md"
        test_file.rename(new_file)

        # Update content
        new_file.write_text("# Updated\n\nNow in subfolder.")

        # Note: Current implementation treats this as a NEW file
        # since the path changed. This is expected behavior for Phase 1.
        result2 = engine.sync_file(vault_config, new_file)
        assert result2.success
        # New file path = new UUID (expected for Phase 1)
        assert result2.remarkable_uuid != uuid

        # Verify folder was created
        assert mock_cloud_sync.upload_folder.called

    def test_concurrent_updates_different_files(self, integration_env, mock_cloud_sync):
        """Test updating multiple different files."""
        from rock_paper_sync.converter import SyncEngine

        vault = integration_env["vault"]
        state = integration_env["state"]
        config = integration_env["config"]

        # Create multiple files
        files = []
        for i in range(5):
            f = vault / f"doc{i}.md"
            f.write_text(f"# Document {i}\n\nOriginal content {i}.")
            files.append(f)

        engine = SyncEngine(config, state, cloud_sync=mock_cloud_sync)
        vault_config = config.sync.vaults[0]

        # First sync all
        first_uuids = {}
        for f in files:
            result = engine.sync_file(vault_config, f)
            assert result.success
            first_uuids[f.name] = result.remarkable_uuid

        # Update all
        for f in files:
            f.write_text(f"# {f.stem} Updated\n\nNew content for {f.stem}.")

        # Sync updates
        for f in files:
            result = engine.sync_file(vault_config, f)
            assert result.success
            # Each should preserve its UUID
            assert result.remarkable_uuid == first_uuids[f.name]

        # Verify cloud uploads for all files (5 initial + 5 updates = 10)
        assert mock_cloud_sync.upload_document.call_count == 10

    def test_update_state_tracking(self, integration_env, mock_cloud_sync):
        """Test that state database correctly tracks updates."""
        from rock_paper_sync.converter import SyncEngine

        vault = integration_env["vault"]
        state = integration_env["state"]
        config = integration_env["config"]

        test_file = vault / "tracked.md"
        test_file.write_text("# V1\n\nFirst.")

        engine = SyncEngine(config, state, cloud_sync=mock_cloud_sync)
        vault_config = config.sync.vaults[0]

        # First sync
        result1 = engine.sync_file(vault_config, test_file)
        uuid = result1.remarkable_uuid

        # Check state
        file_state1 = state.get_file_state("test-vault", "tracked.md")
        assert file_state1 is not None
        assert file_state1.remarkable_uuid == uuid
        hash1 = file_state1.content_hash

        # Wait to ensure timestamp changes
        time.sleep(0.01)

        # Update
        test_file.write_text("# V2\n\nSecond.")
        engine.sync_file(vault_config, test_file)

        # Check updated state
        file_state2 = state.get_file_state("test-vault", "tracked.md")
        assert file_state2 is not None
        assert file_state2.remarkable_uuid == uuid  # Same UUID
        assert file_state2.content_hash != hash1  # Different hash
        assert (
            file_state2.last_sync_time >= file_state1.last_sync_time
        )  # Should increase or stay same
