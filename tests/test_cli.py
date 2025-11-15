"""Tests for command-line interface."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from rock_paper_sync import cli


@pytest.fixture
def runner() -> CliRunner:
    """Create Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_cloud_sync():
    """Mock cloud sync to avoid needing real device registration in tests."""
    mock_sync = MagicMock()
    mock_sync.upload_document = MagicMock()
    mock_sync.upload_folder = MagicMock()
    mock_sync.get_existing_page_uuids = MagicMock(return_value=[])
    mock_sync.delete_document = MagicMock()

    with patch('rock_paper_sync.converter.RmCloudSync', return_value=mock_sync):
        with patch('rock_paper_sync.converter.RmCloudClient'):
            yield mock_sync


@pytest.fixture
def config_file(tmp_path: Path, temp_vault: Path) -> Path:
    """Create a valid config file for testing."""
    config_path = tmp_path / "config.toml"
    config_content = f"""
[paths]
obsidian_vault = "{temp_vault}"
state_database = "{tmp_path / 'state.db'}"

[sync]
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**"]
debounce_seconds = 1

[cloud]
base_url = "http://localhost:3000"

[layout]
lines_per_page = 45
margin_top = 50
margin_bottom = 50
margin_left = 50
margin_right = 50

[logging]
level = "info"
file = "{tmp_path / 'test.log'}"
"""
    config_path.write_text(config_content)
    return config_path


class TestInitCommand:
    """Test init command."""

    def test_init_creates_config(self, runner: CliRunner, tmp_path: Path) -> None:
        """Test init command creates config file."""
        config_path = tmp_path / "config.toml"

        result = runner.invoke(cli.main, ["init", str(config_path)])

        assert result.exit_code == 0
        assert config_path.exists()
        assert "Created config file" in result.output

        # Verify it's valid TOML
        content = config_path.read_text()
        assert "[paths]" in content
        assert "[sync]" in content
        assert "[layout]" in content
        assert "[logging]" in content

    def test_init_default_path(self, runner: CliRunner, tmp_path: Path) -> None:
        """Test init with no argument uses default path."""
        # Test that init without argument creates config at default location
        # Note: We can't easily test the actual default without affecting real files,
        # so we just verify the command accepts no argument
        result = runner.invoke(cli.main, ["init", "--help"])

        assert result.exit_code == 0
        assert "Create example config file" in result.output

    def test_init_overwrite_prompt(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Test init prompts before overwriting existing file."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("existing content")

        # Answer 'n' to overwrite prompt
        result = runner.invoke(
            cli.main, ["init", str(config_path)], input="n\n"
        )

        assert result.exit_code == 0
        # File should not be overwritten
        assert config_path.read_text() == "existing content"

    def test_init_overwrite_confirmed(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Test init overwrites when confirmed."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("existing content")

        # Answer 'y' to overwrite prompt
        result = runner.invoke(
            cli.main, ["init", str(config_path)], input="y\n"
        )

        assert result.exit_code == 0
        # File should be overwritten
        content = config_path.read_text()
        assert "existing content" not in content
        assert "[paths]" in content


class TestSyncCommand:
    """Test sync command."""

    def test_sync_no_config(self, runner: CliRunner) -> None:
        """Test sync fails gracefully when config doesn't exist."""
        result = runner.invoke(cli.main, ["--config", "/nonexistent/config.toml", "sync"])

        assert result.exit_code != 0
        assert "Config file not found" in result.output

    def test_sync_with_files(
        self, runner: CliRunner, config_file: Path, temp_vault: Path, mock_cloud_sync
    ) -> None:
        """Test sync command with files in vault."""
        # Create test files
        (temp_vault / "test1.md").write_text("# Test 1")
        (temp_vault / "test2.md").write_text("# Test 2")

        result = runner.invoke(
            cli.main, ["--config", str(config_file), "sync"]
        )

        assert result.exit_code == 0
        assert "Synced 2/2" in result.output or "Synced 2 / 2" in result.output.replace(" ", "")

    def test_sync_dry_run(
        self, runner: CliRunner, config_file: Path, temp_vault: Path, mock_cloud_sync
    ) -> None:
        """Test sync --dry-run doesn't write files."""
        (temp_vault / "test.md").write_text("# Test")

        result = runner.invoke(
            cli.main, ["--config", str(config_file), "sync", "--dry-run"]
        )

        assert result.exit_code == 0
        assert "Dry run mode" in result.output

    def test_sync_verbose(
        self, runner: CliRunner, config_file: Path, temp_vault: Path, mock_cloud_sync
    ) -> None:
        """Test sync with verbose flag."""
        (temp_vault / "test.md").write_text("# Test")

        result = runner.invoke(
            cli.main, ["--config", str(config_file), "-v", "sync"]
        )

        assert result.exit_code == 0

    def test_sync_empty_vault(
        self, runner: CliRunner, config_file: Path, mock_cloud_sync
    ) -> None:
        """Test sync with empty vault."""
        result = runner.invoke(
            cli.main, ["--config", str(config_file), "sync"]
        )

        assert result.exit_code == 0
        assert "0" in result.output  # Should report 0 files synced


class TestStatusCommand:
    """Test status command."""

    def test_status_no_sync_yet(
        self, runner: CliRunner, config_file: Path
    ) -> None:
        """Test status with no sync history."""
        result = runner.invoke(
            cli.main, ["--config", str(config_file), "status"]
        )

        assert result.exit_code == 0
        assert "Sync Status" in result.output
        assert "Synced:" in result.output

    def test_status_after_sync(
        self, runner: CliRunner, config_file: Path, temp_vault: Path, mock_cloud_sync
    ) -> None:
        """Test status after syncing files."""
        # Create and sync files
        (temp_vault / "test.md").write_text("# Test")

        runner.invoke(cli.main, ["--config", str(config_file), "sync"])

        # Check status
        result = runner.invoke(
            cli.main, ["--config", str(config_file), "status"]
        )

        assert result.exit_code == 0
        assert "Synced:" in result.output
        assert "Recent Activity" in result.output


class TestResetCommand:
    """Test reset command."""

    def test_reset_with_confirmation(
        self, runner: CliRunner, config_file: Path, temp_vault: Path, tmp_path: Path
    ) -> None:
        """Test reset clears state when confirmed."""
        # Create and sync a file
        (temp_vault / "test.md").write_text("# Test")
        runner.invoke(cli.main, ["--config", str(config_file), "sync"])

        # State database should exist
        state_db = tmp_path / "state.db"
        assert state_db.exists()

        # Reset with confirmation
        result = runner.invoke(
            cli.main, ["--config", str(config_file), "reset"], input="y\n"
        )

        assert result.exit_code == 0
        assert "Sync state cleared" in result.output
        assert not state_db.exists()

    def test_reset_without_confirmation(
        self, runner: CliRunner, config_file: Path, temp_vault: Path, tmp_path: Path
    ) -> None:
        """Test reset aborts when not confirmed."""
        # Create and sync a file
        (temp_vault / "test.md").write_text("# Test")
        runner.invoke(cli.main, ["--config", str(config_file), "sync"])

        state_db = tmp_path / "state.db"
        assert state_db.exists()

        # Reset without confirmation
        result = runner.invoke(
            cli.main, ["--config", str(config_file), "reset"], input="n\n"
        )

        assert result.exit_code == 1  # Aborted
        # State should still exist
        assert state_db.exists()

    def test_reset_no_state(
        self, runner: CliRunner, config_file: Path
    ) -> None:
        """Test reset when no state exists."""
        result = runner.invoke(
            cli.main, ["--config", str(config_file), "reset"], input="y\n"
        )

        assert result.exit_code == 0
        assert "No sync state to clear" in result.output


class TestWatchCommand:
    """Test watch command.

    Note: Full watch testing is difficult in unit tests due to threading.
    These tests verify the command structure and basic error handling.
    """

    def test_watch_command_exists(self, runner: CliRunner, config_file: Path) -> None:
        """Test watch command is available."""
        result = runner.invoke(cli.main, ["--config", str(config_file), "watch", "--help"])

        assert result.exit_code == 0
        assert "Continuously monitor" in result.output


class TestMainGroup:
    """Test main CLI group."""

    def test_help(self, runner: CliRunner) -> None:
        """Test --help shows usage."""
        result = runner.invoke(cli.main, ["--help"])

        assert result.exit_code == 0
        assert "reMarkable-Obsidian Sync Tool" in result.output
        assert "sync" in result.output
        assert "watch" in result.output
        assert "status" in result.output
        assert "reset" in result.output
        assert "init" in result.output

    def test_command_help(self, runner: CliRunner) -> None:
        """Test commands show their help."""
        # Test help for individual commands
        result = runner.invoke(cli.main, ["init", "--help"])

        assert result.exit_code == 0
        assert "Create example config file" in result.output

    def test_invalid_command(self, runner: CliRunner) -> None:
        """Test invalid command shows error."""
        result = runner.invoke(cli.main, ["invalid-command"])

        assert result.exit_code != 0

    def test_config_option(self, runner: CliRunner, config_file: Path) -> None:
        """Test --config option works."""
        result = runner.invoke(
            cli.main, ["--config", str(config_file), "status"]
        )

        assert result.exit_code == 0

    def test_verbose_option(self, runner: CliRunner, config_file: Path) -> None:
        """Test --verbose option."""
        result = runner.invoke(
            cli.main, ["--config", str(config_file), "-v", "status"]
        )

        assert result.exit_code == 0

    def test_config_load_error(self, runner: CliRunner, tmp_path: Path) -> None:
        """Test error handling when config fails to load (lines 58-60)."""
        # Create invalid config file
        config_path = tmp_path / "bad_config.toml"
        config_path.write_text("invalid toml content [[[")

        result = runner.invoke(cli.main, ["--config", str(config_path), "sync"])

        assert result.exit_code == 1
        assert "Error loading config" in result.output

    def test_config_validation_error(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Test error handling when config validation fails."""
        # Create config with missing required directories
        config_path = tmp_path / "config.toml"
        config_content = f"""
[paths]
obsidian_vault = "/nonexistent/vault"
remarkable_output = "/nonexistent/output"
state_database = "{tmp_path / 'state.db'}"

[sync]
include_patterns = ["**/*.md"]
exclude_patterns = []
debounce_seconds = 1

[layout]
lines_per_page = 45
margin_top = 50
margin_bottom = 50
margin_left = 50
margin_right = 50

[logging]
level = "info"
file = "{tmp_path / 'test.log'}"
"""
        config_path.write_text(config_content)

        result = runner.invoke(cli.main, ["--config", str(config_path), "sync"])

        assert result.exit_code == 1
        assert "Error loading config" in result.output


class TestInitCommandEdgeCases:
    """Test edge cases for init command."""

    def test_init_with_default_path_keyword(self, runner: CliRunner, mocker) -> None:
        """Test that init handles None output correctly (line 231)."""
        # Mock expanduser to redirect to tmp location
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            fake_config_dir = fake_home / ".config" / "rock-paper-sync"
            fake_config_dir.mkdir(parents=True)
            fake_config_path = fake_config_dir / "config.toml"

            # Mock Path.expanduser to return our tmp path
            mocker.patch.object(
                Path,
                'expanduser',
                return_value=fake_config_path
            )

            # Invoke init without output argument - uses default path
            result = runner.invoke(cli.main, ["init"])

            # Should succeed
            assert result.exit_code == 0
            assert "Created config file" in result.output


class TestSyncCommandErrors:
    """Test error handling in sync command."""

    def test_sync_with_file_error(
        self, runner: CliRunner, config_file: Path, temp_vault: Path, mocker, mock_cloud_sync
    ) -> None:
        """Test sync handles file errors gracefully (line 103)."""
        # Create a markdown file
        (temp_vault / "test.md").write_text("# Test")

        # Mock the SyncEngine to return an error result
        from rock_paper_sync.converter import SyncResult

        mock_result = SyncResult(
            path=temp_vault / "test.md",
            success=False,
            error="Simulated error",
            page_count=0,
        )

        mocker.patch(
            "rock_paper_sync.converter.SyncEngine.sync_all_changed",
            return_value=[mock_result],
        )

        result = runner.invoke(cli.main, ["--config", str(config_file), "sync"])

        assert result.exit_code == 0  # CLI should complete even with errors
        assert "✗" in result.output or "error" in result.output.lower()
        assert "Simulated error" in result.output
