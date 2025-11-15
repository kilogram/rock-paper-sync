"""
Shared pytest fixtures for rock-paper-sync tests.
"""
import pytest
from pathlib import Path
import tempfile
from unittest.mock import MagicMock


@pytest.fixture
def sample_markdown_dir() -> Path:
    """Path to sample markdown fixtures"""
    return Path(__file__).parent / "fixtures" / "sample_markdown"


@pytest.fixture
def simple_markdown(sample_markdown_dir: Path) -> str:
    """Load simple.md fixture"""
    return (sample_markdown_dir / "simple.md").read_text()


@pytest.fixture
def comprehensive_markdown(sample_markdown_dir: Path) -> str:
    """Load comprehensive.md fixture"""
    return (sample_markdown_dir / "comprehensive.md").read_text()


@pytest.fixture
def temp_vault(tmp_path: Path) -> Path:
    """Create temporary Obsidian vault directory"""
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def temp_output(tmp_path: Path) -> Path:
    """Create temporary reMarkable output directory"""
    output = tmp_path / "remarkable_output"
    output.mkdir()
    return output


@pytest.fixture
def temp_state_db(tmp_path: Path) -> Path:
    """Path for temporary state database"""
    return tmp_path / "state.db"


@pytest.fixture
def temp_vault2(tmp_path: Path) -> Path:
    """Create second temporary Obsidian vault directory"""
    vault = tmp_path / "vault2"
    vault.mkdir()
    return vault


@pytest.fixture
def sample_config(temp_vault: Path, temp_state_db: Path):
    """Create sample AppConfig for testing with single vault"""
    # Import here to avoid circular imports during test collection
    from rock_paper_sync.config import AppConfig, SyncConfig, LayoutConfig, CloudConfig, VaultConfig

    return AppConfig(
        sync=SyncConfig(
            vaults=[
                VaultConfig(
                    name="test-vault",
                    path=temp_vault,
                    remarkable_folder="Test Vault",
                    include_patterns=["**/*.md"],
                    exclude_patterns=[".obsidian/**"],
                )
            ],
            state_database=temp_state_db,
            debounce_seconds=1
        ),
        cloud=CloudConfig(
            base_url="http://localhost:3000"
        ),
        layout=LayoutConfig(
            lines_per_page=45,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        ),
        log_level="debug",
        log_file=temp_state_db.parent / "test.log"
    )


@pytest.fixture
def multi_vault_config(temp_vault: Path, temp_vault2: Path, temp_state_db: Path):
    """Create sample AppConfig with multiple vaults for testing"""
    from rock_paper_sync.config import AppConfig, SyncConfig, LayoutConfig, CloudConfig, VaultConfig

    return AppConfig(
        sync=SyncConfig(
            vaults=[
                VaultConfig(
                    name="personal",
                    path=temp_vault,
                    remarkable_folder="Personal",
                    include_patterns=["**/*.md"],
                    exclude_patterns=[".obsidian/**"],
                ),
                VaultConfig(
                    name="work",
                    path=temp_vault2,
                    remarkable_folder="Work",
                    include_patterns=["**/*.md"],
                    exclude_patterns=["archive/**"],
                )
            ],
            state_database=temp_state_db,
            debounce_seconds=1
        ),
        cloud=CloudConfig(
            base_url="http://localhost:3000"
        ),
        layout=LayoutConfig(
            lines_per_page=45,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        ),
        log_level="debug",
        log_file=temp_state_db.parent / "test.log"
    )


@pytest.fixture
def state_manager(temp_state_db: Path):
    """Create StateManager instance for testing"""
    from rock_paper_sync.state import StateManager
    
    manager = StateManager(temp_state_db)
    yield manager
    manager.close()


@pytest.fixture
def markdown_with_frontmatter() -> str:
    """Sample markdown with YAML frontmatter"""
    return """---
title: Test Document
author: Test Author
tags:
  - test
  - fixture
date: 2024-11-15
---

# Introduction

This is the main content after frontmatter.

## Section

More content here.
"""


@pytest.fixture
def markdown_with_formatting() -> str:
    """Sample markdown with various formatting"""
    return """# Formatted Document

This paragraph has **bold text** and *italic text*.

It also has ***bold and italic*** together.

Here's `inline code` too.

## Lists

- Item with **bold**
- Item with *italic*
- Plain item
"""


@pytest.fixture
def long_markdown() -> str:
    """Generate long markdown document for pagination testing"""
    paragraphs = []
    for i in range(100):
        paragraphs.append(
            f"## Section {i+1}\n\n"
            f"This is paragraph {i+1} of the test document. "
            f"It contains enough text to take up multiple lines when rendered. "
            f"The purpose is to test pagination logic and ensure that long documents "
            f"are properly split across multiple pages. Each section should be "
            f"distinct and the page breaks should occur at logical boundaries.\n"
        )
    return "\n".join(paragraphs)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create temporary database path (without initializing)."""
    return tmp_path / "test_state.db"


@pytest.fixture
def valid_config_toml(tmp_path: Path, temp_vault: Path) -> Path:
    """Create a valid TOML config file for testing (single vault)."""
    config_path = tmp_path / "config.toml"
    config_content = f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "test-vault"
path = "{temp_vault}"
remarkable_folder = "Test Vault"
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**", "templates/**"]

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
"""
    config_path.write_text(config_content)
    return config_path


@pytest.fixture
def multi_vault_config_toml(tmp_path: Path, temp_vault: Path, temp_vault2: Path) -> Path:
    """Create a valid multi-vault TOML config file for testing."""
    config_path = tmp_path / "multi_config.toml"
    config_content = f"""
[paths]
state_database = "{tmp_path / 'state.db'}"

[[vaults]]
name = "personal"
path = "{temp_vault}"
remarkable_folder = "Personal"
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**"]

[[vaults]]
name = "work"
path = "{temp_vault2}"
remarkable_folder = "Work"
include_patterns = ["**/*.md"]
exclude_patterns = ["archive/**"]

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
"""
    config_path.write_text(config_content)
    return config_path


@pytest.fixture
def config_samples_dir() -> Path:
    """Path to config sample fixtures."""
    return Path(__file__).parent / "fixtures" / "config_samples"


@pytest.fixture
def mock_cloud_sync():
    """Create a mock cloud sync client for testing without real cloud connection."""
    mock_sync = MagicMock()
    mock_sync.upload_document = MagicMock()
    mock_sync.upload_folder = MagicMock()
    mock_sync.get_existing_page_uuids = MagicMock(return_value=[])
    mock_sync.delete_document = MagicMock()
    mock_sync.is_sync_enabled = MagicMock(return_value=True)
    return mock_sync
