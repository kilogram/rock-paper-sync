# reMarkable-Obsidian Sync

Sync your Obsidian markdown notes to reMarkable Paper Pro for reading, annotation, and handwritten note-taking.

## Overview

This tool converts Obsidian markdown files into reMarkable documents, preserving:
- Document structure (headers, paragraphs, lists)
- Text formatting (bold, italic)
- Folder hierarchy
- YAML frontmatter titles

Generated documents appear on your reMarkable device as native notebooks with typed text that you can annotate with handwritten notes.

## Features

- **Multi-vault support**: Sync multiple Obsidian vaults simultaneously
- **Optional folder organization**: Each vault can have its own folder on reMarkable
- **One-way sync**: Obsidian → reMarkable
- **Incremental sync**: Only processes changed files
- **Folder preservation**: Mirrors Obsidian directory structure
- **Watch mode**: Automatically syncs on file changes
- **State tracking**: SQLite database tracks sync status per vault
- **Configurable**: TOML configuration for paths, patterns, layout

## Requirements

- Python 3.10+
- reMarkable Paper Pro (firmware 3.0+)
- rm_cloud server or reMarkable cloud connection
- One or more Obsidian vaults with markdown files

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/rock-paper-sync.git
cd rock-paper-sync

# Install with uv (recommended)
uv sync

# Or with pip
pip install -e ".[dev]"
```

## Configuration

1. Create config directory:
```bash
mkdir -p ~/.config/rock-paper-sync
```

2. Initialize configuration:
```bash
rock-paper-sync init ~/.config/rock-paper-sync/config.toml
```

3. Edit the generated config file to add your vaults:
```toml
[paths]
state_database = "~/.local/share/rock-paper-sync/state.db"

# Define your Obsidian vaults
[[vaults]]
name = "personal"
path = "~/obsidian-vault-personal"
remarkable_folder = "Personal Notes"  # Optional - creates folder on reMarkable
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**", "templates/**"]

[[vaults]]
name = "work"
path = "~/obsidian-vault-work"
remarkable_folder = "Work"  # Optional - creates folder on reMarkable
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**", "archive/**"]

# Example: Vault without folder (files go to root)
# NOTE: Only one vault can omit remarkable_folder when multiple vaults are configured
# [[vaults]]
# name = "quick-notes"
# path = "~/obsidian-quick"
# include_patterns = ["**/*.md"]
# exclude_patterns = []

[sync]
debounce_seconds = 5

[cloud]
base_url = "http://localhost:3000"  # or https://webapp-prod.cloud.remarkable.com

[logging]
level = "info"
file = "~/.local/share/rock-paper-sync/sync.log"
```

4. Register your device with rm_cloud:
```bash
rock-paper-sync register <one-time-code>
```

Get the one-time code from rm_cloud web UI or the reMarkable mobile app.

## Usage

### Sync All Vaults

```bash
rock-paper-sync sync
```

### Sync Specific Vault

```bash
rock-paper-sync sync --vault personal
```

### Watch for Changes (Continuous Sync)

Watch all vaults:
```bash
rock-paper-sync watch
```

Watch specific vault:
```bash
rock-paper-sync watch --vault work
```

### Check Sync Status

Status for all vaults:
```bash
rock-paper-sync status
```

Status for specific vault:
```bash
rock-paper-sync status --vault personal
```

### Reset Sync State

```bash
rock-paper-sync reset
```

### Create Example Config

```bash
rock-paper-sync init ~/.config/rock-paper-sync/config.toml
```

## How It Works

1. **File Detection**: Scans Obsidian vault for markdown files matching include patterns
2. **Change Detection**: Computes SHA-256 hash to detect actual content changes
3. **Markdown Parsing**: Converts markdown to structured content blocks with formatting
4. **Pagination**: Splits content into pages (~45 lines per page)
5. **RM Generation**: Creates reMarkable v6 format files using rmscene library
6. **Metadata Creation**: Generates proper `.metadata` and `.content` files
7. **State Update**: Records sync in SQLite database for incremental sync

## File Structure

Generated reMarkable documents have this structure:

```
{document-uuid}/
├── {document-uuid}.metadata    # Document properties
├── {document-uuid}.content     # Page list and settings
├── {page-1-uuid}.rm           # Page 1 content (v6 binary)
├── {page-1-uuid}-metadata.json # Page 1 settings
├── {page-2-uuid}.rm           # Page 2 content
└── {page-2-uuid}-metadata.json # Page 2 settings
```

## Limitations

### Current Phase (Phase 1)
- **One-way sync only**: Changes on reMarkable are not synced back
- **Text-only**: No support for embedded images, diagrams
- **Basic formatting**: Only bold and italic (no colors, highlights)
- **No math rendering**: LaTeX equations appear as plain text
- **No table rendering**: Tables converted to text representation

### Known Issues
- rmscene's write API is experimental
- Very long documents (100+ pages) may be slow to generate
- Some markdown extensions not supported

## Development

```bash
# Run tests
uv run pytest

# Format and lint
uv run black src/ tests/
uv run ruff check src/ tests/

# Type check
uv run mypy src/
```

See `CLAUDE.md` for detailed development workflow.

## Project Structure

```
rock-paper-sync/
├── CLAUDE.md              # Development guide
├── README.md              # This file
├── docs/
│   ├── MULTI_VAULT.md     # Multi-vault configuration
│   ├── SYNC_PROTOCOL.md   # Sync v3 protocol details
│   └── RMSCENE_FINDINGS.md # reMarkable format notes
├── src/rock_paper_sync/
│   ├── cli.py             # Command-line interface
│   ├── config.py          # Configuration loading
│   ├── parser.py          # Markdown parsing
│   ├── generator.py       # RM file generation
│   ├── metadata.py        # RM metadata creation
│   ├── rm_cloud_sync.py   # Cloud sync protocol
│   ├── converter.py       # Sync orchestration
│   └── state.py           # SQLite state management
└── tests/                 # Test suite
```

## Future

- Annotation preservation (see `src/rock_paper_sync/annotations/README.md`)
- Bidirectional sync
- Image embedding

## Contributing

1. Check `CLAUDE.md` for development guidelines
2. Read technical docs in `docs/`
3. Submit PRs with tests

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- [rmscene](https://github.com/ricklupton/rmscene) - reMarkable v6 file format library
- [mistune](https://github.com/lepture/mistune) - Markdown parser
- [watchdog](https://github.com/gorakhargosh/watchdog) - File system monitoring
- reMarkable community for format documentation
