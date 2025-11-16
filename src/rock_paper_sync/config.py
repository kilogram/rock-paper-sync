"""Configuration management for reMarkable-Obsidian Sync.

This module handles loading and validating TOML configuration files.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Python 3.11+ has tomllib built-in, 3.10 needs tomli
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover (Python 3.10 compatibility)
    try:
        import tomli as tomllib  # type: ignore[import-not-found,no-redef]
    except ImportError:
        raise ImportError(
            "tomli is required for Python < 3.11. Install with: pip install tomli"
        )


class ConfigError(Exception):
    """Exception raised for configuration errors."""

    pass


@dataclass(frozen=True)
class VaultConfig:
    """Configuration for a single Obsidian vault."""

    name: str
    path: Path
    remarkable_folder: Optional[str]  # None = files go to root
    include_patterns: list[str]
    exclude_patterns: list[str]


@dataclass(frozen=True)
class SyncConfig:
    """Synchronization configuration."""

    vaults: list[VaultConfig]
    state_database: Path
    debounce_seconds: int


@dataclass(frozen=True)
class LayoutConfig:
    """Page layout configuration.

    Attributes:
        lines_per_page: Maximum lines per page
        margin_top: Top margin in pixels (used for text positioning)
        margin_bottom: Bottom margin in pixels (used for text positioning)
        margin_left: Left margin in pixels (used for text positioning)
        margin_right: Right margin in pixels (used for text positioning)
        allow_paragraph_splitting: Whether to allow paragraphs to split across pages
                                   (True = better page utilization, False = atomic paragraphs, default: False)
    """

    lines_per_page: int
    margin_top: int
    margin_bottom: int
    margin_left: int
    margin_right: int
    allow_paragraph_splitting: bool = False


@dataclass(frozen=True)
class CloudConfig:
    """reMarkable cloud integration configuration."""

    base_url: str


@dataclass(frozen=True)
class AppConfig:
    """Complete application configuration."""

    sync: SyncConfig
    layout: LayoutConfig
    log_level: str
    log_file: Path
    cloud: CloudConfig


def expand_path(path_str: str) -> Path:
    """Expand ~ and environment variables in path string.

    Args:
        path_str: Path string that may contain ~ or $VAR references

    Returns:
        Expanded Path object

    Example:
        >>> expand_path("~/documents")
        Path('/home/user/documents')
    """
    # Expand environment variables first
    expanded = os.path.expandvars(path_str)
    # Then expand user home directory
    return Path(expanded).expanduser()


def load_config(config_path: Path) -> AppConfig:
    """Load configuration from TOML file.

    Args:
        config_path: Path to TOML configuration file

    Returns:
        Validated AppConfig object

    Raises:
        ConfigError: If config file is missing, invalid, or incomplete
    """
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, "rb") as f:
            config_dict = tomllib.load(f)
    except Exception as e:
        raise ConfigError(f"Failed to parse TOML configuration: {e}")

    try:
        # Extract paths section
        paths = config_dict.get("paths", {})
        if not paths:
            raise ConfigError("Missing required [paths] section in config")

        state_database = paths.get("state_database")
        if not state_database:
            raise ConfigError("Missing required field: paths.state_database")

        # Extract vaults - array of vault configurations
        vaults_config = config_dict.get("vaults", [])
        if not vaults_config:
            raise ConfigError(
                "Missing required [[vaults]] section in config\n"
                "At least one vault must be configured."
            )

        # Parse vault configurations
        vaults = []
        for i, vault_dict in enumerate(vaults_config):
            name = vault_dict.get("name")
            if not name:
                raise ConfigError(f"Vault at index {i} is missing required 'name' field")

            path = vault_dict.get("path")
            if not path:
                raise ConfigError(f"Vault '{name}' is missing required 'path' field")

            remarkable_folder = vault_dict.get("remarkable_folder")  # Optional
            include_patterns = vault_dict.get("include_patterns", ["**/*.md"])
            exclude_patterns = vault_dict.get("exclude_patterns", [])

            vaults.append(
                VaultConfig(
                    name=name,
                    path=expand_path(path),
                    remarkable_folder=remarkable_folder,
                    include_patterns=include_patterns,
                    exclude_patterns=exclude_patterns,
                )
            )

        # Extract sync section for global settings
        sync = config_dict.get("sync", {})
        debounce_seconds = sync.get("debounce_seconds", 5) if sync else 5

        # Extract layout section
        layout = config_dict.get("layout", {})
        if not layout:
            raise ConfigError("Missing required [layout] section in config")

        lines_per_page = layout.get("lines_per_page", 28)
        margin_top = layout.get("margin_top", 50)
        margin_bottom = layout.get("margin_bottom", 50)
        margin_left = layout.get("margin_left", 50)
        margin_right = layout.get("margin_right", 50)
        allow_paragraph_splitting = layout.get("allow_paragraph_splitting", False)

        # Extract logging section
        logging_config = config_dict.get("logging", {})
        if not logging_config:
            raise ConfigError("Missing required [logging] section in config")

        log_level = logging_config.get("level", "info")
        log_file = logging_config.get("file")

        if not log_file:
            raise ConfigError("Missing required field: logging.file")

        # Create configuration objects
        sync_config = SyncConfig(
            vaults=vaults,
            state_database=expand_path(state_database),
            debounce_seconds=debounce_seconds,
        )

        layout_config = LayoutConfig(
            lines_per_page=lines_per_page,
            margin_top=margin_top,
            margin_bottom=margin_bottom,
            margin_left=margin_left,
            margin_right=margin_right,
            allow_paragraph_splitting=allow_paragraph_splitting,
        )

        # Extract cloud section (required)
        cloud = config_dict.get("cloud", {})
        if not cloud:
            raise ConfigError("Missing required [cloud] section in config")

        base_url = cloud.get("base_url")
        if not base_url:
            raise ConfigError("Missing required field: cloud.base_url")

        cloud_config = CloudConfig(base_url=base_url)

        app_config = AppConfig(
            sync=sync_config,
            layout=layout_config,
            log_level=log_level,
            log_file=expand_path(log_file),
            cloud=cloud_config,
        )

        return app_config

    except ConfigError:
        raise
    except Exception as e:
        raise ConfigError(f"Invalid configuration structure: {e}")


def validate_config(config: AppConfig) -> None:
    """Validate that configuration paths exist and are accessible.

    Args:
        config: AppConfig object to validate

    Raises:
        ConfigError: If validation fails with clear error message
    """
    # Validate vaults
    if not config.sync.vaults:
        raise ConfigError("No vaults configured. At least one vault is required.")

    # Check vault names are unique
    vault_names = [v.name for v in config.sync.vaults]
    if len(vault_names) != len(set(vault_names)):
        raise ConfigError("Vault names must be unique")

    # Validate: if multiple vaults, at most one can have no remarkable_folder
    if len(config.sync.vaults) > 1:
        vaults_without_folder = [v for v in config.sync.vaults if v.remarkable_folder is None]
        if len(vaults_without_folder) > 1:
            vault_list = ", ".join(f"'{v.name}'" for v in vaults_without_folder)
            raise ConfigError(
                f"When multiple vaults are configured, at most one vault can omit 'remarkable_folder'.\n"
                f"Found {len(vaults_without_folder)} vaults without folders: {vault_list}\n"
                f"Please specify a 'remarkable_folder' for all but one vault to avoid mixing files in the root."
            )

    # Validate each vault
    for vault in config.sync.vaults:
        if not vault.path.exists():
            raise ConfigError(
                f"Vault '{vault.name}' directory does not exist: {vault.path}\n"
                "Please create the directory or update the vault path in your config."
            )

        if not vault.path.is_dir():
            raise ConfigError(
                f"Vault '{vault.name}' path is not a directory: {vault.path}"
            )

        if not os.access(vault.path, os.R_OK):
            raise ConfigError(
                f"Vault '{vault.name}' directory is not readable: {vault.path}\n"
                "Please check file permissions."
            )

        if not vault.include_patterns:
            raise ConfigError(
                f"Vault '{vault.name}' has no include_patterns.\n"
                "Specify at least one pattern, e.g., ['**/*.md']"
            )

    # No output directory validation needed - we use cloud API only!

    # Validate state database directory is writable (create if needed)
    db_dir = config.sync.state_database.parent
    if not db_dir.exists():
        try:
            db_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise ConfigError(
                f"Cannot create state database directory: {db_dir}\n"
                f"Error: {e}"
            )

    if not os.access(db_dir, os.W_OK):
        raise ConfigError(
            f"State database directory is not writable: {db_dir}\n"
            "Please check file permissions."
        )

    # Validate log file directory is writable (create if needed)
    log_dir = config.log_file.parent
    if not log_dir.exists():
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise ConfigError(
                f"Cannot create log file directory: {log_dir}\n" f"Error: {e}"
            )

    if not os.access(log_dir, os.W_OK):
        raise ConfigError(
            f"Log file directory is not writable: {log_dir}\n"
            "Please check file permissions."
        )

    # Validate numeric values are positive
    if config.sync.debounce_seconds < 0:
        raise ConfigError(
            f"debounce_seconds must be positive, got: {config.sync.debounce_seconds}"
        )

    if config.layout.lines_per_page <= 0:
        raise ConfigError(
            f"lines_per_page must be positive, got: {config.layout.lines_per_page}"
        )

    for margin_name in ["margin_top", "margin_bottom", "margin_left", "margin_right"]:
        margin_value = getattr(config.layout, margin_name)
        if margin_value < 0:
            raise ConfigError(f"{margin_name} must be non-negative, got: {margin_value}")

    # Validate log level
    valid_log_levels = ["debug", "info", "warning", "error", "critical"]
    if config.log_level.lower() not in valid_log_levels:
        raise ConfigError(
            f"Invalid log level: {config.log_level}\n"
            f"Must be one of: {', '.join(valid_log_levels)}"
        )

