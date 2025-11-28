"""Tests for multi-vault configuration support."""

from pathlib import Path

import pytest

from rock_paper_sync.config import (
    ConfigError,
    load_config,
    validate_config,
)


class TestMultiVaultLoading:
    """Tests for loading multi-vault configurations."""

    def test_load_single_vault(self, valid_config_toml: Path) -> None:
        """Test loading a config with a single vault."""
        config = load_config(valid_config_toml)

        assert len(config.sync.vaults) == 1
        assert config.sync.vaults[0].name == "test-vault"
        assert config.sync.vaults[0].remarkable_folder == "Test Vault"
        assert config.sync.vaults[0].include_patterns == ["**/*.md"]

    def test_load_multi_vault(self, multi_vault_config_toml: Path) -> None:
        """Test loading a config with multiple vaults."""
        config = load_config(multi_vault_config_toml)

        assert len(config.sync.vaults) == 2
        assert config.sync.vaults[0].name == "personal"
        assert config.sync.vaults[0].remarkable_folder == "Personal"
        assert config.sync.vaults[1].name == "work"
        assert config.sync.vaults[1].remarkable_folder == "Work"

    def test_missing_vaults_section(self, tmp_path: Path) -> None:
        """Test that missing [[vaults]] section raises ConfigError."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        with pytest.raises(ConfigError, match="Missing required \\[\\[vaults\\]\\] section"):
            load_config(config_path)

    def test_vault_missing_name(self, tmp_path: Path, temp_vault: Path) -> None:
        """Test that vault without name raises ConfigError."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
path = "{temp_vault}"
remarkable_folder = "Test"
include_patterns = ["**/*.md"]
exclude_patterns = []

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        with pytest.raises(ConfigError, match="missing required 'name' field"):
            load_config(config_path)

    def test_vault_missing_path(self, tmp_path: Path) -> None:
        """Test that vault without path raises ConfigError."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "test"
remarkable_folder = "Test"
include_patterns = ["**/*.md"]
exclude_patterns = []

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        with pytest.raises(ConfigError, match="missing required 'path' field"):
            load_config(config_path)

    def test_vault_without_remarkable_folder(self, tmp_path: Path, temp_vault: Path) -> None:
        """Test that vault can omit remarkable_folder (files go to root)."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "test"
path = "{temp_vault}"
# No remarkable_folder
include_patterns = ["**/*.md"]
exclude_patterns = []

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        config = load_config(config_path)
        assert config.sync.vaults[0].remarkable_folder is None


class TestMultiVaultValidation:
    """Tests for multi-vault configuration validation."""

    def test_validate_single_vault_with_folder(self, valid_config_toml: Path) -> None:
        """Test that single vault with folder passes validation."""
        config = load_config(valid_config_toml)
        # Should not raise
        validate_config(config)

    def test_validate_single_vault_without_folder(self, tmp_path: Path, temp_vault: Path) -> None:
        """Test that single vault without folder passes validation."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "test"
path = "{temp_vault}"
# No remarkable_folder
include_patterns = ["**/*.md"]
exclude_patterns = []

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        config = load_config(config_path)
        # Should not raise - single vault can omit folder
        validate_config(config)

    def test_validate_multi_vault_all_with_folders(self, multi_vault_config_toml: Path) -> None:
        """Test that multiple vaults all with folders passes validation."""
        config = load_config(multi_vault_config_toml)
        # Should not raise
        validate_config(config)

    def test_validate_multi_vault_one_without_folder(
        self, tmp_path: Path, temp_vault: Path, temp_vault2: Path
    ) -> None:
        """Test that multiple vaults with one without folder passes validation."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "personal"
path = "{temp_vault}"
# No remarkable_folder - ALLOWED (only one without)
include_patterns = ["**/*.md"]
exclude_patterns = []

[[vaults]]
name = "work"
path = "{temp_vault2}"
remarkable_folder = "Work"
include_patterns = ["**/*.md"]
exclude_patterns = []

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        config = load_config(config_path)
        # Should not raise - only one vault without folder
        validate_config(config)

    def test_validate_multi_vault_two_without_folders_fails(
        self, tmp_path: Path, temp_vault: Path, temp_vault2: Path
    ) -> None:
        """Test that multiple vaults with two without folders fails validation."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "personal"
path = "{temp_vault}"
# No remarkable_folder
include_patterns = ["**/*.md"]
exclude_patterns = []

[[vaults]]
name = "work"
path = "{temp_vault2}"
# No remarkable_folder - SHOULD FAIL
include_patterns = ["**/*.md"]
exclude_patterns = []

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        config = load_config(config_path)

        with pytest.raises(
            ConfigError,
            match="at most one vault can omit 'remarkable_folder'",
        ):
            validate_config(config)

    def test_validate_duplicate_vault_names(
        self, tmp_path: Path, temp_vault: Path, temp_vault2: Path
    ) -> None:
        """Test that duplicate vault names fail validation."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "test"
path = "{temp_vault}"
remarkable_folder = "Test1"
include_patterns = ["**/*.md"]
exclude_patterns = []

[[vaults]]
name = "test"
path = "{temp_vault2}"
remarkable_folder = "Test2"
include_patterns = ["**/*.md"]
exclude_patterns = []

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        config = load_config(config_path)

        with pytest.raises(ConfigError, match="Vault names must be unique"):
            validate_config(config)

    def test_validate_vault_path_doesnt_exist(self, tmp_path: Path, temp_vault: Path) -> None:
        """Test that nonexistent vault path fails validation."""
        config_path = tmp_path / "config.toml"
        nonexistent = tmp_path / "nonexistent_vault"

        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "test"
path = "{nonexistent}"
remarkable_folder = "Test"
include_patterns = ["**/*.md"]
exclude_patterns = []

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        config = load_config(config_path)

        with pytest.raises(ConfigError, match="directory does not exist"):
            validate_config(config)

    def test_validate_vault_path_is_file(self, tmp_path: Path) -> None:
        """Test that vault path being a file fails validation."""
        config_path = tmp_path / "config.toml"
        file_path = tmp_path / "not_a_directory"
        file_path.write_text("I'm a file")

        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "test"
path = "{file_path}"
remarkable_folder = "Test"
include_patterns = ["**/*.md"]
exclude_patterns = []

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        config = load_config(config_path)

        with pytest.raises(ConfigError, match="not a directory"):
            validate_config(config)

    def test_validate_vault_empty_include_patterns(self, tmp_path: Path, temp_vault: Path) -> None:
        """Test that vault with empty include_patterns fails validation."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "test"
path = "{temp_vault}"
remarkable_folder = "Test"
include_patterns = []
exclude_patterns = []

[sync]
debounce_seconds = 5

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
file = "{tmp_path / 'sync.log'}"
""")

        config = load_config(config_path)

        with pytest.raises(ConfigError, match="has no include_patterns"):
            validate_config(config)
