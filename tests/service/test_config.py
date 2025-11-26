"""Tests for configuration module."""

import os
import pytest
from dataclasses import replace
from pathlib import Path

from rock_paper_sync.config import (
    AppConfig,
    ConfigError,
    LayoutConfig,
    OCRConfig,
    SyncConfig,
    VaultConfig,
    expand_path,
    load_config,
    validate_config,
)

from ..config_helpers import make_app_config, make_vault_config, with_sync, with_layout, with_vault


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

        # Check vaults were loaded
        assert len(config.sync.vaults) == 1
        vault = config.sync.vaults[0]
        assert vault.name == "test-vault"
        assert vault.path == temp_vault
        assert vault.remarkable_folder == "Test Vault"

        # Check cloud config
        assert config.cloud.base_url == "http://localhost:3000"

        # Check default values
        assert vault.include_patterns == ["**/*.md"]
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
        with pytest.raises(ConfigError, match="missing required 'path' field"):
            load_config(config_path)

    def test_all_sections_present(self, tmp_path: Path, temp_vault: Path, temp_output: Path) -> None:
        """Test that all required sections must be present."""
        config_path = tmp_path / "incomplete.toml"
        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "test"
path = "{temp_vault}"
remarkable_folder = "Test"
include_patterns = ["**/*.md"]

[cloud]
base_url = "http://localhost:3000"

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

    def test_validate_valid_config(self, tmp_path: Path) -> None:
        """Test that valid config passes validation."""
        config = make_app_config(tmp_path)
        validate_config(config)  # Should not raise

    def test_validate_nonexistent_vault(self, tmp_path: Path) -> None:
        """Test that nonexistent vault directory fails validation."""
        nonexistent = tmp_path / "nonexistent"
        # Create config manually to avoid auto-creating the vault
        from rock_paper_sync.config import AppConfig, SyncConfig, LayoutConfig, CloudConfig, OCRConfig

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(exist_ok=True)

        config = AppConfig(
            sync=SyncConfig(
                vaults=[make_vault_config(path=nonexistent)],
                state_database=tmp_path / "state.db",
                debounce_seconds=1.0,
            ),
            cloud=CloudConfig(base_url="http://localhost:3000"),
            layout=LayoutConfig(
                lines_per_page=45,
                margin_top=50,
                margin_bottom=50,
                margin_left=50,
                margin_right=50,
            ),
            log_level="debug",
            log_file=tmp_path / "test.log",
            ocr=OCRConfig(enabled=False),
            cache_dir=cache_dir,
        )

        with pytest.raises(ConfigError, match="directory does not exist"):
            validate_config(config)

    def test_validate_vault_is_file_not_directory(self, tmp_path: Path) -> None:
        """Test that vault must be a directory, not a file."""
        file_path = tmp_path / "vault_file"
        file_path.write_text("not a directory")

        # Create config manually to avoid auto-creating the vault
        from rock_paper_sync.config import AppConfig, SyncConfig, LayoutConfig, CloudConfig, OCRConfig

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(exist_ok=True)

        config = AppConfig(
            sync=SyncConfig(
                vaults=[make_vault_config(path=file_path)],
                state_database=tmp_path / "state.db",
                debounce_seconds=1.0,
            ),
            cloud=CloudConfig(base_url="http://localhost:3000"),
            layout=LayoutConfig(
                lines_per_page=45,
                margin_top=50,
                margin_bottom=50,
                margin_left=50,
                margin_right=50,
            ),
            log_level="debug",
            log_file=tmp_path / "test.log",
            ocr=OCRConfig(enabled=False),
            cache_dir=cache_dir,
        )

        with pytest.raises(ConfigError, match="not a directory"):
            validate_config(config)

    def test_validate_creates_db_directory(self, tmp_path: Path) -> None:
        """Test that database directory is created if it doesn't exist."""
        new_db_dir = tmp_path / "new_db_dir" / "subdir"
        new_db_path = new_db_dir / "state.db"

        config = make_app_config(tmp_path, state_database=new_db_path)

        validate_config(config)
        assert new_db_dir.exists()
        assert new_db_dir.is_dir()

    def test_validate_creates_log_directory(self, tmp_path: Path) -> None:
        """Test that log file directory is created if it doesn't exist."""
        new_log_dir = tmp_path / "new_log_dir" / "subdir"
        new_log_path = new_log_dir / "sync.log"

        config = make_app_config(tmp_path, log_file=new_log_path)

        validate_config(config)
        assert new_log_dir.exists()
        assert new_log_dir.is_dir()

    def test_validate_negative_debounce(self, tmp_path: Path) -> None:
        """Test that negative debounce_seconds fails validation."""
        config = make_app_config(tmp_path, debounce_seconds=-1)

        with pytest.raises(ConfigError, match="debounce_seconds must be positive"):
            validate_config(config)

    def test_validate_negative_lines_per_page(self, tmp_path: Path) -> None:
        """Test that non-positive lines_per_page fails validation."""
        config = make_app_config(tmp_path, lines_per_page=0)

        with pytest.raises(ConfigError, match="lines_per_page must be positive"):
            validate_config(config)

    @pytest.mark.parametrize("margin_name,margin_value", [
        ("margin_top", -10),
        ("margin_bottom", -5),
        ("margin_left", -5),
        ("margin_right", -5),
    ])
    def test_validate_negative_margins(self, tmp_path: Path, margin_name: str, margin_value: int) -> None:
        """Test that negative margins fail validation."""
        config = make_app_config(tmp_path, **{margin_name: margin_value})

        with pytest.raises(ConfigError, match=f"{margin_name} must be non-negative"):
            validate_config(config)

    def test_validate_invalid_log_level(self, tmp_path: Path) -> None:
        """Test that invalid log level fails validation."""
        config = make_app_config(tmp_path, log_level="invalid_level")

        with pytest.raises(ConfigError, match="Invalid log level"):
            validate_config(config)

    def test_validate_empty_include_patterns(self, tmp_path: Path) -> None:
        """Test that empty include_patterns fails validation."""
        config = make_app_config(
            tmp_path,
            vaults=[make_vault_config(path=tmp_path / "vault", include_patterns=[])],
        )

        with pytest.raises(ConfigError, match="has no include_patterns"):
            validate_config(config)

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
        config_path.write_text('paths = "invalid"')

        with pytest.raises(ConfigError, match="Invalid configuration structure"):
            load_config(config_path)

    def test_vault_permission_error(self, tmp_path: Path, mocker) -> None:
        """Test that unreadable vault directory fails validation."""
        config = make_app_config(tmp_path)

        # Mock os.access to return False for read permission
        mocker.patch("os.access", return_value=False)

        with pytest.raises(ConfigError, match="not readable"):
            validate_config(config)

    def test_state_database_dir_creation_failure(self, tmp_path: Path, mocker) -> None:
        """Test that state database directory creation failure is handled."""
        bad_db = tmp_path / "nonexistent_parent" / "state.db"
        config = make_app_config(tmp_path, state_database=bad_db)

        # Mock mkdir to raise an exception
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("Mock permission error"))

        with pytest.raises(ConfigError, match="Cannot create state database directory"):
            validate_config(config)

    def test_state_database_dir_not_writable(self, tmp_path: Path, mocker) -> None:
        """Test that non-writable state database directory fails validation."""
        db_dir = tmp_path / "state_dir"
        db_dir.mkdir()
        db_path = db_dir / "state.db"

        config = make_app_config(tmp_path, state_database=db_path)

        # Mock os.access to return False for write permission only for state db dir
        def mock_access(path, mode):
            if str(path) == str(db_dir) and mode == os.W_OK:
                return False
            return True

        mocker.patch("os.access", side_effect=mock_access)

        with pytest.raises(ConfigError, match="State database directory is not writable"):
            validate_config(config)

    def test_log_file_dir_creation_failure(self, tmp_path: Path, mocker) -> None:
        """Test that log file directory creation failure is handled."""
        bad_log = tmp_path / "nonexistent_parent" / "sync.log"
        config = make_app_config(tmp_path, log_file=bad_log)

        # Mock mkdir to raise an exception
        mocker.patch.object(Path, "mkdir", side_effect=OSError("Mock OS error"))

        with pytest.raises(ConfigError, match="Cannot create log file directory"):
            validate_config(config)

    def test_log_file_dir_not_writable(self, tmp_path: Path, mocker) -> None:
        """Test that non-writable log file directory fails validation."""
        log_dir = tmp_path / "log_dir"
        log_dir.mkdir()
        log_path = log_dir / "sync.log"

        config = make_app_config(tmp_path, log_file=log_path)

        # Mock os.access to return False for write permission only for log dir
        def mock_access(path, mode):
            if str(path) == str(log_dir) and mode == os.W_OK:
                return False
            return True

        mocker.patch("os.access", side_effect=mock_access)

        with pytest.raises(ConfigError, match="Log file directory is not writable"):
            validate_config(config)
