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
    from rock_paper_sync.config import AppConfig, SyncConfig, LayoutConfig, CloudConfig, VaultConfig, OCRConfig

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
        log_file=temp_state_db.parent / "test.log",
        ocr=OCRConfig(),
    )


@pytest.fixture
def multi_vault_config(temp_vault: Path, temp_vault2: Path, temp_state_db: Path):
    """Create sample AppConfig with multiple vaults for testing"""
    from rock_paper_sync.config import AppConfig, SyncConfig, LayoutConfig, CloudConfig, VaultConfig, OCRConfig

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
        log_file=temp_state_db.parent / "test.log",
        ocr=OCRConfig(),
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


# OCR Testing Fixtures

@pytest.fixture
def ocr_config(tmp_path: Path):
    """Create OCRConfig for testing."""
    from rock_paper_sync.config import OCRConfig

    cache_dir = tmp_path / "ocr_cache"
    cache_dir.mkdir()

    return OCRConfig(
        enabled=True,
        provider="runpods",
        model_version="latest",
        confidence_threshold=0.7,
        timeout=30.0,
        container_runtime="podman",
        local_image="rock-paper-sync/ocr:latest",
        local_gpu_device="cpu",
        runpods_endpoint_id="test-endpoint",
        runpods_api_key="test-key",
        cache_dir=cache_dir,
        min_corrections_for_dataset=10,
        auto_fine_tune=False,
        base_model="microsoft/trocr-base-handwritten",
        use_lora=True,
    )


@pytest.fixture
def sample_config_with_ocr(temp_vault: Path, temp_state_db: Path, tmp_path: Path):
    """Create sample AppConfig with OCR enabled for testing."""
    from rock_paper_sync.config import (
        AppConfig, SyncConfig, LayoutConfig, CloudConfig, VaultConfig, OCRConfig
    )

    cache_dir = tmp_path / "ocr_cache"
    cache_dir.mkdir()

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
        log_file=temp_state_db.parent / "test.log",
        ocr=OCRConfig(
            enabled=True,
            provider="runpods",
            cache_dir=cache_dir,
            confidence_threshold=0.7,
            min_corrections_for_dataset=10,
        ),
    )


@pytest.fixture
def markdown_with_ocr_markers() -> str:
    """Sample markdown with OCR markers."""
    return """# Test Document

This is the first paragraph without annotations.

<!-- RPS:ANNOTATED highlights=2 strokes=1 -->
This is an annotated paragraph with highlights and strokes.
<!-- RPS:OCR -->
handwritten note here
another line of handwriting
<!-- RPS:END -->

This is another plain paragraph.

<!-- RPS:ANNOTATED highlights=0 strokes=3 -->
Second annotated paragraph.
<!-- RPS:OCR -->
more handwriting
<!-- RPS:END -->
"""


@pytest.fixture
def markdown_with_corrected_ocr() -> str:
    """Sample markdown with user-corrected OCR text."""
    return """# Test Document

<!-- RPS:ANNOTATED highlights=1 strokes=1 -->
Original paragraph text here.
<!-- RPS:OCR -->
corrected handwriting text
<!-- RPS:END -->
"""


@pytest.fixture
def mock_ocr_service():
    """Create a mock OCR service for testing."""
    from rock_paper_sync.ocr.protocol import OCRResult, ModelInfo, BoundingBox, ParagraphContext
    from datetime import datetime

    mock_service = MagicMock()

    # Default recognize response
    def mock_recognize(request):
        return OCRResult(
            annotation_uuid=request.annotation_uuid,
            text="recognized text",
            confidence=0.95,
            model_version="test-v1",
            bounding_box=request.bounding_box,
            context=request.context,
            processing_time_ms=100,
        )

    def mock_recognize_batch(requests):
        return [mock_recognize(req) for req in requests]

    mock_service.recognize = MagicMock(side_effect=mock_recognize)
    mock_service.recognize_batch = MagicMock(side_effect=mock_recognize_batch)
    mock_service.health_check = MagicMock(return_value=True)
    mock_service.get_model_info = MagicMock(return_value=ModelInfo(
        version="test-v1",
        base_model="microsoft/trocr-base-handwritten",
        is_fine_tuned=False,
        dataset_version=None,
        created_at=datetime.now(),
        metrics={},
    ))

    return mock_service
