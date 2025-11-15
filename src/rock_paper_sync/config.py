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
class SyncConfig:
    """Synchronization configuration."""

    obsidian_vault: Path
    remarkable_output: Path
    state_database: Path
    include_patterns: list[str]
    exclude_patterns: list[str]
    debounce_seconds: int


@dataclass(frozen=True)
class LayoutConfig:
    """Page layout configuration."""

    lines_per_page: int
    margin_top: int
    margin_bottom: int
    margin_left: int
    margin_right: int


@dataclass(frozen=True)
class RmCloudConfig:
    """rm_cloud integration configuration (optional)."""

    enabled: bool
    base_url: str


@dataclass(frozen=True)
class AppConfig:
    """Complete application configuration."""

    sync: SyncConfig
    layout: LayoutConfig
    log_level: str
    log_file: Path
    rm_cloud: Optional[RmCloudConfig] = None


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

        obsidian_vault = paths.get("obsidian_vault")
        remarkable_output = paths.get("remarkable_output")
        state_database = paths.get("state_database")

        if not obsidian_vault:
            raise ConfigError("Missing required field: paths.obsidian_vault")
        if not remarkable_output:
            raise ConfigError("Missing required field: paths.remarkable_output")
        if not state_database:
            raise ConfigError("Missing required field: paths.state_database")

        # Extract sync section
        sync = config_dict.get("sync", {})
        if not sync:
            raise ConfigError("Missing required [sync] section in config")

        include_patterns = sync.get("include_patterns", ["**/*.md"])
        exclude_patterns = sync.get("exclude_patterns", [])
        debounce_seconds = sync.get("debounce_seconds", 5)

        # Extract layout section
        layout = config_dict.get("layout", {})
        if not layout:
            raise ConfigError("Missing required [layout] section in config")

        lines_per_page = layout.get("lines_per_page", 45)
        margin_top = layout.get("margin_top", 50)
        margin_bottom = layout.get("margin_bottom", 50)
        margin_left = layout.get("margin_left", 50)
        margin_right = layout.get("margin_right", 50)

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
            obsidian_vault=expand_path(obsidian_vault),
            remarkable_output=expand_path(remarkable_output),
            state_database=expand_path(state_database),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            debounce_seconds=debounce_seconds,
        )

        layout_config = LayoutConfig(
            lines_per_page=lines_per_page,
            margin_top=margin_top,
            margin_bottom=margin_bottom,
            margin_left=margin_left,
            margin_right=margin_right,
        )

        # Extract optional rm_cloud section
        rm_cloud_config = None
        rm_cloud = config_dict.get("rm_cloud", {})
        if rm_cloud.get("enabled", False):
            base_url = rm_cloud.get("base_url", "http://localhost:3000")

            rm_cloud_config = RmCloudConfig(
                enabled=True,
                base_url=base_url,
            )

        app_config = AppConfig(
            sync=sync_config,
            layout=layout_config,
            log_level=log_level,
            log_file=expand_path(log_file),
            rm_cloud=rm_cloud_config,
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
    # Validate obsidian vault exists and is readable
    if not config.sync.obsidian_vault.exists():
        raise ConfigError(
            f"Obsidian vault directory does not exist: {config.sync.obsidian_vault}\n"
            "Please create the directory or update paths.obsidian_vault in your config."
        )

    if not config.sync.obsidian_vault.is_dir():
        raise ConfigError(
            f"Obsidian vault path is not a directory: {config.sync.obsidian_vault}"
        )

    if not os.access(config.sync.obsidian_vault, os.R_OK):
        raise ConfigError(
            f"Obsidian vault directory is not readable: {config.sync.obsidian_vault}\n"
            "Please check file permissions."
        )

    # Validate remarkable output directory exists and is writable
    if not config.sync.remarkable_output.exists():
        raise ConfigError(
            f"reMarkable output directory does not exist: {config.sync.remarkable_output}\n"
            "Please create the directory or update paths.remarkable_output in your config."
        )

    if not config.sync.remarkable_output.is_dir():
        raise ConfigError(
            f"reMarkable output path is not a directory: {config.sync.remarkable_output}"
        )

    if not os.access(config.sync.remarkable_output, os.W_OK):
        raise ConfigError(
            f"reMarkable output directory is not writable: {config.sync.remarkable_output}\n"
            "Please check file permissions."
        )

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

    # Validate patterns are reasonable (basic check)
    if not config.sync.include_patterns:
        raise ConfigError(
            "include_patterns cannot be empty\n"
            "Specify at least one pattern, e.g., ['**/*.md']"
        )
