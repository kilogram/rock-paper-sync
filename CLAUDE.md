# Rock-Paper-Sync - Development Guide

## Overview

**Purpose**: One-way sync tool to convert Obsidian markdown files into reMarkable Paper Pro documents via cloud API.

**Status**: ✅ Production-ready with multi-vault support

## Quick Reference

### Architecture

```
[Obsidian Vaults] → [Parser] → [Generator] → [Cloud API] → [reMarkable Device]
       ↓               ↓            ↓             ↓              ↓
    .md files      mistune      rmscene      Sync v3        xochitl
                   AST          .rm files    protocol       (viewer)
```

### Key Components

1. **parser.py** - Markdown parsing with mistune
   - See `parse_markdown_file()` docstring for format details
   - Handles frontmatter, formatting, lists, code blocks

2. **generator.py** - reMarkable document generation
   - See `RemarkableGenerator` class docstring for pagination logic
   - Creates v6 binary .rm files via rmscene library

3. **metadata.py** - Cloud metadata generation
   - See module docstring for CRDT format details
   - Generates .metadata, .content, .local files

4. **rm_cloud_sync.py** - Sync v3 protocol implementation
   - See `RmCloudSync` class docstring for protocol details
   - Implements hashOfHashesV3 algorithm

5. **config.py** - Multi-vault configuration
   - See `VaultConfig` docstring for configuration options
   - Validates vault setup rules

6. **state.py** - Vault-aware state tracking
   - See `StateManager` class docstring for database schema
   - Schema v2 with composite primary keys

7. **converter.py** - Sync orchestration
   - See `SyncEngine` class docstring for workflow
   - Coordinates parser, generator, and cloud sync

8. **cli.py** - Command-line interface
   - See command function docstrings for usage

### Critical Implementation Details

**For detailed technical information, see code docstrings:**

- Sync v3 Protocol: `src/rock_paper_sync/rm_cloud_sync.py` docstring
- CRDT Format: `src/rock_paper_sync/metadata.py` module docstring
- Pagination Algorithm: `RemarkableGenerator.paginate_content()` docstring
- Multi-Vault Rules: `validate_config()` in `config.py`

**External references:**
- Sync v3 Protocol: `docs/SYNC_PROTOCOL.md`
- Multi-Vault Guide: `docs/MULTI_VAULT.md`
- rmscene Findings: `docs/RMSCENE_FINDINGS.md`

## Development Workflow

### Important: Always Use `uv run`

**All Python commands must be prefixed with `uv run`** to ensure correct dependency resolution:

```bash
# ✅ Correct
uv run pytest
uv run python -m rock_paper_sync.cli
uv run python -c "import rmscene; print(rmscene.__version__)"

# ❌ Wrong
pytest
python -m rock_paper_sync.cli
python -c "import rmscene; print(rmscene.__version__)"
```

This ensures the virtual environment managed by `uv` is used with all dependencies available.

### Running Tests

```bash
# Core tests
uv run pytest tests/test_state.py tests/test_converter.py tests/test_multi_vault_config.py -v

# All tests
uv run pytest

# With coverage
uv run pytest --cov=src/rock_paper_sync --cov-report=term-missing
```

### Adding Features

1. **New Vault Feature**
   - Update `VaultConfig` in `config.py`
   - Update validation in `validate_config()`
   - Update tests in `test_multi_vault_config.py`
   - Update `docs/MULTI_VAULT.md`

2. **New Sync Feature**
   - Update relevant component (parser/generator/sync)
   - Add comprehensive docstrings
   - Write tests
   - Update README.md usage section

3. **New CLI Command**
   - Add command in `cli.py` with detailed docstring
   - Update README.md with usage example

### Code Style

- Follow existing patterns in codebase
- Comprehensive docstrings (see examples in code)
- Type hints required
- Tests required for new functionality

## Configuration

See `docs/MULTI_VAULT.md` for vault configuration details and examples.

**Key Rules**:
- Vault names must be unique
- When multiple vaults: at most ONE can omit `remarkable_folder`

## Milestones

### ✅ Milestone 1: Core Sync
- Markdown → reMarkable conversion
- Basic formatting preservation
- Multi-page documents
- State tracking
- 99%+ test coverage

### ✅ Milestone 2: Cloud Sync
- Sync v3 protocol implementation
- hashOfHashesV3 algorithm
- CRDT formatVersion 2
- File deletion support
- Live device validation

### ✅ Milestone 3: Multi-Vault
- Multiple vault configuration
- Optional folder organization
- Vault-aware state (schema v2)
- Per-vault CLI filtering
- 195 core tests passing

### ✅ Milestone 4: Annotation System
- Generation-based annotation detection
- Three-way merge (content + annotations)
- Snapshot-based restoration
- Content-addressable storage
- Automatic cleanup (7-day retention)
- 668 tests passing

### 🔮 Future Considerations
- Full bidirectional sync
- Image embedding
- Custom templates
- Batch operations

## Troubleshooting

### Common Issues

**Import Errors**: Run `uv sync` to ensure dependencies are installed

**Test Failures**: Check you're using `uv run pytest` not plain `pytest`

**Config Errors**: Validate config with `rock-paper-sync init --help`

**Sync Issues**: Check logs at `~/.local/share/rock-paper-sync/sync.log`

### Debug Mode

```bash
# Enable debug logging
[logging]
level = "debug"
```

## Key Files

- `CLAUDE.md` - This file (development guide)
- `README.md` - User documentation
- `docs/MULTI_VAULT.md` - Multi-vault user guide
- `docs/SYNC_PROTOCOL.md` - Technical protocol reference
- `docs/RMSCENE_FINDINGS.md` - rmscene library notes
- `src/rock_paper_sync/annotations/README.md` - Annotation system architecture

## Getting Help

1. Check code docstrings for technical details
2. Review tests for usage examples
3. Check `docs/` for specific topics
4. Enable debug logging for troubleshooting
