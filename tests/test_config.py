"""Tests for configuration module."""

import os
import pytest
from pathlib import Path

from rock_paper_sync.config import (
    AppConfig,
    ConfigError,
    LayoutConfig,
    SyncConfig,
    expand_path,
    load_config,
    validate_config,
)


class TestExpandPath:
    """Tests for path expansion utility."""

    def test_expand_tilde(self, tmp_path: Path) -> None:
        """Test that ~ is expanded to home directory."""
        home = Path.home()
        expanded = expand_path("~/test")
        assert expanded == home / "test"

    def test_expand_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that environment variables are expanded."""
        monkeypatch.setenv("TEST_DIR", str(tmp_path))
        expanded = expand_path("$TEST_DIR/subdir")
        assert expanded == tmp_path / "subdir"

    def test_expand_both(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test expansion of both ~ and env vars."""
        monkeypatch.setenv("SUBDIR", "documents")
        expanded = expand_path("~/$SUBDIR/test")
        assert expanded == Path.home() / "documents" / "test"

    def test_no_expansion_needed(self) -> None:
        """Test that absolute paths are left unchanged."""
        path = "/absolute/path/test"
        expanded = expand_path(path)
        assert expanded == Path(path)


class TestLoadConfig:
    """Tests for configuration loading."""

    def test_load_valid_config(self, valid_config_toml: Path, temp_vault: Path, temp_output: Path) -> None:
        """Test loading a valid configuration file."""
        config = load_config(valid_config_toml)

        assert isinstance(config, AppConfig)
        assert isinstance(config.sync, SyncConfig)
        assert isinstance(config.layout, LayoutConfig)

        # Check paths were expanded
        assert config.sync.obsidian_vault == temp_vault
        assert config.sync.remarkable_output == temp_output

        # Check default values
        assert config.sync.include_patterns == ["**/*.md"]
        assert config.sync.debounce_seconds == 5
        assert config.layout.lines_per_page == 45
        assert config.log_level == "info"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        """Test that missing config file raises ConfigError."""
        missing_path = tmp_path / "nonexistent.toml"
        with pytest.raises(ConfigError, match="Configuration file not found"):
            load_config(missing_path)

    def test_load_invalid_toml(self, config_samples_dir: Path) -> None:
        """Test that invalid TOML syntax raises ConfigError."""
        invalid_toml = config_samples_dir / "invalid_toml.toml"
        with pytest.raises(ConfigError, match="Failed to parse TOML"):
            load_config(invalid_toml)

    def test_missing_paths_section(self, config_samples_dir: Path) -> None:
        """Test that missing [paths] section raises ConfigError."""
        config_path = config_samples_dir / "missing_paths_section.toml"
        with pytest.raises(ConfigError, match="Missing required \\[paths\\] section"):
            load_config(config_path)

    def test_missing_required_field(self, config_samples_dir: Path) -> None:
        """Test that missing required field raises ConfigError."""
        config_path = config_samples_dir / "missing_vault_path.toml"
        with pytest.raises(ConfigError, match="Missing required field: paths.obsidian_vault"):
            load_config(config_path)

    def test_all_sections_present(self, tmp_path: Path, temp_vault: Path, temp_output: Path) -> None:
        """Test that all required sections must be present."""
        # Test missing sync section
        config_path = tmp_path / "no_sync.toml"
        config_path.write_text(f"""
[paths]
obsidian_vault = "{temp_vault}"
remarkable_output = "{temp_output}"
state_database = "{tmp_path / 'state.db'}"

[layout]
lines_per_page = 45
margin_top = 50
margin_bottom = 50
margin_left = 50
margin_right = 50

[logging]
level = "info"
file = "{tmp_path / 'sync.log'}"
""")
        with pytest.raises(ConfigError, match="Missing required \\[sync\\] section"):
            load_config(config_path)

        # Test missing layout section
        config_path = tmp_path / "no_layout.toml"
        config_path.write_text(f"""
[paths]
obsidian_vault = "{temp_vault}"
remarkable_output = "{temp_output}"
state_database = "{tmp_path / 'state.db'}"

[sync]
include_patterns = ["**/*.md"]
exclude_patterns = []
debounce_seconds = 5

[logging]
level = "info"
file = "{tmp_path / 'sync.log'}"
""")
        with pytest.raises(ConfigError, match="Missing required \\[layout\\] section"):
            load_config(config_path)

        # Test missing logging section
        config_path = tmp_path / "no_logging.toml"
        config_path.write_text(f"""
[paths]
obsidian_vault = "{temp_vault}"
remarkable_output = "{temp_output}"
state_database = "{tmp_path / 'state.db'}"

[sync]
include_patterns = ["**/*.md"]
exclude_patterns = []
debounce_seconds = 5

[layout]
lines_per_page = 45
margin_top = 50
margin_bottom = 50
margin_left = 50
margin_right = 50
""")
        with pytest.raises(ConfigError, match="Missing required \\[logging\\] section"):
            load_config(config_path)


class TestValidateConfig:
    """Tests for configuration validation."""

    def test_validate_valid_config(self, valid_config_toml: Path, temp_vault: Path, temp_output: Path) -> None:
        """Test that valid config passes validation."""
        config = load_config(valid_config_toml)
        # Should not raise any exception
        validate_config(config)

    def test_validate_nonexistent_vault(self, valid_config_toml: Path, tmp_path: Path) -> None:
        """Test that nonexistent vault directory fails validation."""
        config = load_config(valid_config_toml)
        # Modify vault path to nonexistent directory
        nonexistent_vault = tmp_path / "nonexistent_vault"
        config = AppConfig(
            sync=SyncConfig(
                obsidian_vault=nonexistent_vault,
                remarkable_output=config.sync.remarkable_output,
                state_database=config.sync.state_database,
                include_patterns=config.sync.include_patterns,
                exclude_patterns=config.sync.exclude_patterns,
                debounce_seconds=config.sync.debounce_seconds,
            ),
            layout=config.layout,
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="Obsidian vault directory does not exist"):
            validate_config(config)

    def test_validate_vault_is_file_not_directory(self, valid_config_toml: Path, tmp_path: Path) -> None:
        """Test that vault must be a directory, not a file."""
        config = load_config(valid_config_toml)
        # Create a file instead of directory
        file_path = tmp_path / "vault_file"
        file_path.write_text("not a directory")

        config = AppConfig(
            sync=SyncConfig(
                obsidian_vault=file_path,
                remarkable_output=config.sync.remarkable_output,
                state_database=config.sync.state_database,
                include_patterns=config.sync.include_patterns,
                exclude_patterns=config.sync.exclude_patterns,
                debounce_seconds=config.sync.debounce_seconds,
            ),
            layout=config.layout,
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="not a directory"):
            validate_config(config)

    def test_validate_nonexistent_output(self, valid_config_toml: Path, tmp_path: Path) -> None:
        """Test that nonexistent output directory fails validation."""
        config = load_config(valid_config_toml)
        nonexistent_output = tmp_path / "nonexistent_output"

        config = AppConfig(
            sync=SyncConfig(
                obsidian_vault=config.sync.obsidian_vault,
                remarkable_output=nonexistent_output,
                state_database=config.sync.state_database,
                include_patterns=config.sync.include_patterns,
                exclude_patterns=config.sync.exclude_patterns,
                debounce_seconds=config.sync.debounce_seconds,
            ),
            layout=config.layout,
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="reMarkable output directory does not exist"):
            validate_config(config)

    def test_validate_creates_db_directory(self, valid_config_toml: Path, tmp_path: Path) -> None:
        """Test that database directory is created if it doesn't exist."""
        config = load_config(valid_config_toml)
        new_db_dir = tmp_path / "new_db_dir" / "subdir"
        new_db_path = new_db_dir / "state.db"

        config = AppConfig(
            sync=SyncConfig(
                obsidian_vault=config.sync.obsidian_vault,
                remarkable_output=config.sync.remarkable_output,
                state_database=new_db_path,
                include_patterns=config.sync.include_patterns,
                exclude_patterns=config.sync.exclude_patterns,
                debounce_seconds=config.sync.debounce_seconds,
            ),
            layout=config.layout,
            log_level=config.log_level,
            log_file=config.log_file,
        )

        # Should create directory
        validate_config(config)
        assert new_db_dir.exists()
        assert new_db_dir.is_dir()

    def test_validate_creates_log_directory(self, valid_config_toml: Path, tmp_path: Path) -> None:
        """Test that log file directory is created if it doesn't exist."""
        config = load_config(valid_config_toml)
        new_log_dir = tmp_path / "new_log_dir" / "subdir"
        new_log_path = new_log_dir / "sync.log"

        config = AppConfig(
            sync=config.sync,
            layout=config.layout,
            log_level=config.log_level,
            log_file=new_log_path,
        )

        # Should create directory
        validate_config(config)
        assert new_log_dir.exists()
        assert new_log_dir.is_dir()

    def test_validate_negative_debounce(self, valid_config_toml: Path) -> None:
        """Test that negative debounce_seconds fails validation."""
        config = load_config(valid_config_toml)
        config = AppConfig(
            sync=SyncConfig(
                obsidian_vault=config.sync.obsidian_vault,
                remarkable_output=config.sync.remarkable_output,
                state_database=config.sync.state_database,
                include_patterns=config.sync.include_patterns,
                exclude_patterns=config.sync.exclude_patterns,
                debounce_seconds=-1,
            ),
            layout=config.layout,
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="debounce_seconds must be positive"):
            validate_config(config)

    def test_validate_negative_lines_per_page(self, valid_config_toml: Path) -> None:
        """Test that non-positive lines_per_page fails validation."""
        config = load_config(valid_config_toml)
        config = AppConfig(
            sync=config.sync,
            layout=LayoutConfig(
                lines_per_page=0,
                margin_top=config.layout.margin_top,
                margin_bottom=config.layout.margin_bottom,
                margin_left=config.layout.margin_left,
                margin_right=config.layout.margin_right,
            ),
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="lines_per_page must be positive"):
            validate_config(config)

    def test_validate_negative_margins(self, valid_config_toml: Path) -> None:
        """Test that negative margins fail validation."""
        config = load_config(valid_config_toml)
        config = AppConfig(
            sync=config.sync,
            layout=LayoutConfig(
                lines_per_page=config.layout.lines_per_page,
                margin_top=-10,
                margin_bottom=config.layout.margin_bottom,
                margin_left=config.layout.margin_left,
                margin_right=config.layout.margin_right,
            ),
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="margin_top must be non-negative"):
            validate_config(config)

    def test_validate_invalid_log_level(self, valid_config_toml: Path) -> None:
        """Test that invalid log level fails validation."""
        config = load_config(valid_config_toml)
        config = AppConfig(
            sync=config.sync,
            layout=config.layout,
            log_level="invalid_level",
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="Invalid log level"):
            validate_config(config)

    def test_validate_empty_include_patterns(self, valid_config_toml: Path) -> None:
        """Test that empty include_patterns fails validation."""
        config = load_config(valid_config_toml)
        config = AppConfig(
            sync=SyncConfig(
                obsidian_vault=config.sync.obsidian_vault,
                remarkable_output=config.sync.remarkable_output,
                state_database=config.sync.state_database,
                include_patterns=[],
                exclude_patterns=config.sync.exclude_patterns,
                debounce_seconds=config.sync.debounce_seconds,
            ),
            layout=config.layout,
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="include_patterns cannot be empty"):
            validate_config(config)

    def test_missing_remarkable_output(self, config_samples_dir: Path) -> None:
        """Test that missing remarkable_output field raises ConfigError."""
        config_path = config_samples_dir / "missing_output_path.toml"
        with pytest.raises(ConfigError, match="Missing required field: paths.remarkable_output"):
            load_config(config_path)

    def test_missing_state_database(self, config_samples_dir: Path) -> None:
        """Test that missing state_database field raises ConfigError."""
        config_path = config_samples_dir / "missing_state_db.toml"
        with pytest.raises(ConfigError, match="Missing required field: paths.state_database"):
            load_config(config_path)

    def test_missing_log_file(self, config_samples_dir: Path) -> None:
        """Test that missing log file field raises ConfigError."""
        config_path = config_samples_dir / "missing_log_file.toml"
        with pytest.raises(ConfigError, match="Missing required field: logging.file"):
            load_config(config_path)

    def test_invalid_config_structure(self, tmp_path: Path) -> None:
        """Test that invalid config structure raises ConfigError."""
        config_path = tmp_path / "invalid_structure.toml"
        # Create TOML with invalid structure (e.g., paths is a string not a table)
        config_path.write_text('paths = "invalid"')

        with pytest.raises(ConfigError, match="Invalid configuration structure"):
            load_config(config_path)

    def test_vault_permission_error(self, valid_config_toml: Path, tmp_path: Path, mocker) -> None:
        """Test that unreadable vault directory fails validation."""
        config = load_config(valid_config_toml)

        # Mock os.access to return False for read permission
        mocker.patch("os.access", return_value=False)

        with pytest.raises(ConfigError, match="not readable"):
            validate_config(config)

    def test_validate_negative_margin_bottom(self, valid_config_toml: Path) -> None:
        """Test that negative margin_bottom fails validation."""
        config = load_config(valid_config_toml)
        config = AppConfig(
            sync=config.sync,
            layout=LayoutConfig(
                lines_per_page=config.layout.lines_per_page,
                margin_top=config.layout.margin_top,
                margin_bottom=-5,
                margin_left=config.layout.margin_left,
                margin_right=config.layout.margin_right,
            ),
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="margin_bottom must be non-negative"):
            validate_config(config)

    def test_validate_negative_margin_left(self, valid_config_toml: Path) -> None:
        """Test that negative margin_left fails validation."""
        config = load_config(valid_config_toml)
        config = AppConfig(
            sync=config.sync,
            layout=LayoutConfig(
                lines_per_page=config.layout.lines_per_page,
                margin_top=config.layout.margin_top,
                margin_bottom=config.layout.margin_bottom,
                margin_left=-5,
                margin_right=config.layout.margin_right,
            ),
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="margin_left must be non-negative"):
            validate_config(config)

    def test_validate_negative_margin_right(self, valid_config_toml: Path) -> None:
        """Test that negative margin_right fails validation."""
        config = load_config(valid_config_toml)
        config = AppConfig(
            sync=config.sync,
            layout=LayoutConfig(
                lines_per_page=config.layout.lines_per_page,
                margin_top=config.layout.margin_top,
                margin_bottom=config.layout.margin_bottom,
                margin_left=config.layout.margin_left,
                margin_right=-5,
            ),
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="margin_right must be non-negative"):
            validate_config(config)

    def test_remarkable_output_not_directory(self, valid_config_toml: Path, tmp_path: Path) -> None:
        """Test that output path being a file (not directory) fails validation."""
        config = load_config(valid_config_toml)
        # Create a file instead of a directory
        file_path = tmp_path / "output_file.txt"
        file_path.write_text("not a directory")

        config = AppConfig(
            sync=SyncConfig(
                obsidian_vault=config.sync.obsidian_vault,
                remarkable_output=file_path,
                state_database=config.sync.state_database,
                include_patterns=config.sync.include_patterns,
                exclude_patterns=config.sync.exclude_patterns,
                debounce_seconds=config.sync.debounce_seconds,
            ),
            layout=config.layout,
            log_level=config.log_level,
            log_file=config.log_file,
        )

        with pytest.raises(ConfigError, match="is not a directory"):
            validate_config(config)

    def test_remarkable_output_not_writable(self, valid_config_toml: Path, mocker) -> None:
        """Test that non-writable output directory fails validation."""
        config = load_config(valid_config_toml)

        # Mock os.access to return False for write permission only for output dir
        def mock_access(path, mode):
            if str(path) == str(config.sync.remarkable_output) and mode == os.W_OK:
                return False
            return True

        mocker.patch("os.access", side_effect=mock_access)

        with pytest.raises(ConfigError, match="not writable"):
            validate_config(config)

    def test_state_database_dir_creation_failure(self, valid_config_toml: Path, tmp_path: Path, mocker) -> None:
        """Test that state database directory creation failure is handled."""
        config = load_config(valid_config_toml)

        # Set state_database to a path with a non-creatable parent
        bad_db = tmp_path / "nonexistent_parent" / "state.db"
        config = AppConfig(
            sync=SyncConfig(
                obsidian_vault=config.sync.obsidian_vault,
                remarkable_output=config.sync.remarkable_output,
                state_database=bad_db,
                include_patterns=config.sync.include_patterns,
                exclude_patterns=config.sync.exclude_patterns,
                debounce_seconds=config.sync.debounce_seconds,
            ),
            layout=config.layout,
            log_level=config.log_level,
            log_file=config.log_file,
        )

        # Mock mkdir to raise an exception
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("Mock permission error"))

        with pytest.raises(ConfigError, match="Cannot create state database directory"):
            validate_config(config)

    def test_state_database_dir_not_writable(self, valid_config_toml: Path, tmp_path: Path, mocker) -> None:
        """Test that non-writable state database directory fails validation."""
        config = load_config(valid_config_toml)

        # Create a valid database path
        db_dir = tmp_path / "state_dir"
        db_dir.mkdir()
        db_path = db_dir / "state.db"

        config = AppConfig(
            sync=SyncConfig(
                obsidian_vault=config.sync.obsidian_vault,
                remarkable_output=config.sync.remarkable_output,
                state_database=db_path,
                include_patterns=config.sync.include_patterns,
                exclude_patterns=config.sync.exclude_patterns,
                debounce_seconds=config.sync.debounce_seconds,
            ),
            layout=config.layout,
            log_level=config.log_level,
            log_file=config.log_file,
        )

        # Mock os.access to return False for write permission only for state db dir
        def mock_access(path, mode):
            if str(path) == str(db_dir) and mode == os.W_OK:
                return False
            return True

        mocker.patch("os.access", side_effect=mock_access)

        with pytest.raises(ConfigError, match="State database directory is not writable"):
            validate_config(config)

    def test_log_file_dir_creation_failure(self, valid_config_toml: Path, tmp_path: Path, mocker) -> None:
        """Test that log file directory creation failure is handled."""
        config = load_config(valid_config_toml)

        # Set log_file to a path with a non-creatable parent
        bad_log = tmp_path / "nonexistent_parent" / "sync.log"
        config = AppConfig(
            sync=config.sync,
            layout=config.layout,
            log_level=config.log_level,
            log_file=bad_log,
        )

        # Mock mkdir to raise an exception
        mocker.patch.object(Path, "mkdir", side_effect=OSError("Mock OS error"))

        with pytest.raises(ConfigError, match="Cannot create log file directory"):
            validate_config(config)

    def test_log_file_dir_not_writable(self, valid_config_toml: Path, tmp_path: Path, mocker) -> None:
        """Test that non-writable log file directory fails validation."""
        config = load_config(valid_config_toml)

        # Create a valid log path
        log_dir = tmp_path / "log_dir"
        log_dir.mkdir()
        log_path = log_dir / "sync.log"

        config = AppConfig(
            sync=config.sync,
            layout=config.layout,
            log_level=config.log_level,
            log_file=log_path,
        )

        # Mock os.access to return False for write permission only for log dir
        def mock_access(path, mode):
            if str(path) == str(log_dir) and mode == os.W_OK:
                return False
            return True

        mocker.patch("os.access", side_effect=mock_access)

        with pytest.raises(ConfigError, match="Log file directory is not writable"):
            validate_config(config)
