"""Test helpers for configuration-related tests.

Provides factory functions and utilities to reduce boilerplate when testing
configuration validation and loading.
"""

from dataclasses import replace
from pathlib import Path

from rock_paper_sync.config import (
    AppConfig,
    CloudConfig,
    LayoutConfig,
    OCRConfig,
    SyncConfig,
    VaultConfig,
)


def make_vault_config(
    name: str = "test-vault",
    path: Path | None = None,
    remarkable_folder: str = "Test",
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> VaultConfig:
    """Create a VaultConfig with sensible defaults."""
    return VaultConfig(
        name=name,
        path=path or Path("/tmp/test-vault"),
        remarkable_folder=remarkable_folder,
        include_patterns=["**/*.md"] if include_patterns is None else include_patterns,
        exclude_patterns=[] if exclude_patterns is None else exclude_patterns,
    )


def make_app_config(
    tmp_path: Path,
    vaults: list[VaultConfig] | None = None,
    state_database: Path | None = None,
    debounce_seconds: float = 1.0,
    margin_top: int = 50,
    margin_bottom: int = 50,
    margin_left: int = 50,
    margin_right: int = 50,
    log_level: str = "debug",
    log_file: Path | None = None,
    base_url: str = "http://localhost:3000",
    ocr_enabled: bool = False,
    allow_paragraph_splitting: bool = False,
    **layout_overrides,
) -> AppConfig:
    """Create an AppConfig with sensible defaults.

    All paths are created relative to tmp_path to ensure test isolation.

    Args:
        tmp_path: pytest tmp_path fixture for isolation
        vaults: List of vault configs (defaults to single test vault)
        state_database: Path to state DB (defaults to tmp_path/state.db)
        debounce_seconds: Sync debounce delay
        margin_*: Page margins
        log_level: Logging level
        log_file: Log file path (defaults to tmp_path/test.log)
        base_url: Cloud API URL
        ocr_enabled: Whether OCR is enabled
        allow_paragraph_splitting: Whether paragraphs can split across pages
        **layout_overrides: Additional layout overrides

    Returns:
        Fully configured AppConfig
    """
    # Create default vault in tmp_path
    if vaults is None:
        vault_path = tmp_path / "vault"
        vault_path.mkdir(exist_ok=True)
        vaults = [make_vault_config(path=vault_path)]

    # Ensure all vault paths exist
    for vault in vaults:
        vault.path.mkdir(parents=True, exist_ok=True)

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)

    return AppConfig(
        sync=SyncConfig(
            vaults=vaults,
            state_database=state_database or tmp_path / "state.db",
            debounce_seconds=debounce_seconds,
        ),
        cloud=CloudConfig(base_url=base_url),
        layout=LayoutConfig(
            margin_top=margin_top,
            margin_bottom=margin_bottom,
            margin_left=margin_left,
            margin_right=margin_right,
            allow_paragraph_splitting=allow_paragraph_splitting,
        ),
        log_level=log_level,
        log_file=log_file or tmp_path / "test.log",
        ocr=OCRConfig(enabled=ocr_enabled),
        cache_dir=cache_dir,
    )


def with_sync(config: AppConfig, **kwargs) -> AppConfig:
    """Return config with modified SyncConfig fields.

    Example:
        config = with_sync(config, debounce_seconds=-1)
    """
    new_sync = replace(config.sync, **kwargs)
    return replace(config, sync=new_sync)


def with_layout(config: AppConfig, **kwargs) -> AppConfig:
    """Return config with modified LayoutConfig fields.

    Example:
        config = with_layout(config, lines_per_page=0)
    """
    new_layout = replace(config.layout, **kwargs)
    return replace(config, layout=new_layout)


def with_vault(config: AppConfig, vault_index: int = 0, **kwargs) -> AppConfig:
    """Return config with modified vault fields.

    Example:
        config = with_vault(config, path=Path("/nonexistent"))
    """
    vaults = list(config.sync.vaults)
    vaults[vault_index] = replace(vaults[vault_index], **kwargs)
    new_sync = replace(config.sync, vaults=vaults)
    return replace(config, sync=new_sync)


def with_vaults(config: AppConfig, vaults: list[VaultConfig]) -> AppConfig:
    """Return config with replaced vaults list.

    Example:
        config = with_vaults(config, [vault1, vault2])
    """
    new_sync = replace(config.sync, vaults=vaults)
    return replace(config, sync=new_sync)
