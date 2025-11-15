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

- **One-way sync**: Obsidian → reMarkable (Phase 1)
- **Incremental sync**: Only processes changed files
- **Folder preservation**: Mirrors Obsidian directory structure
- **Watch mode**: Automatically syncs on file changes
- **State tracking**: SQLite database tracks sync status
- **Configurable**: TOML configuration for paths, patterns, layout

## Requirements

- Python 3.10+
- reMarkable Paper Pro (firmware 3.0+)
- Syncthing (or similar) for file transfer to device
- Obsidian vault with markdown files

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/rock-paper-sync.git
cd rock-paper-sync

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install in development mode
pip install -e ".[dev]"
```

## Configuration

1. Create config directory:
```bash
mkdir -p ~/.config/rock-paper-sync
```

2. Copy and edit example config:
```bash
cp example_config.toml ~/.config/rock-paper-sync/config.toml
```

3. Edit paths in config file:
```toml
[paths]
obsidian_vault = "/path/to/your/obsidian/vault"
remarkable_output = "/path/to/syncthing/remarkable/folder"
state_database = "~/.local/share/rock-paper-sync/state.db"
```

## Usage

### Sync All Changed Files

```bash
rock-paper-sync sync
```

### Watch for Changes (Continuous Sync)

```bash
rock-paper-sync watch
```

### Check Sync Status

```bash
rock-paper-sync status
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

### Running Tests

```bash
pytest tests/
```

### Code Quality

```bash
# Format code
black src/ tests/

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

### Test Coverage

```bash
pytest --cov=rock_paper_sync tests/
```

## Project Structure

```
remarkable-obsidian-sync/
├── CLAUDE.md              # Claude Code instructions
├── docs/
│   ├── REQUIREMENTS.md    # Detailed requirements
│   ├── ARCHITECTURE.md    # Technical architecture
│   └── TASKS.md          # Implementation tasks
├── src/rock_paper_sync/
│   ├── cli.py            # Command-line interface
│   ├── config.py         # Configuration loading
│   ├── watcher.py        # File system monitoring
│   ├── parser.py         # Markdown parsing
│   ├── converter.py      # Sync orchestration
│   ├── generator.py      # RM file generation
│   ├── metadata.py       # RM metadata creation
│   └── state.py          # SQLite state management
├── tests/
│   └── fixtures/         # Test markdown files
├── pyproject.toml        # Project configuration
└── example_config.toml   # Example config file
```

## Roadmap

### Phase 1 (Current): Obsidian → reMarkable
- Convert markdown to readable reMarkable documents
- Preserve folder structure
- Track sync state

### Phase 2 (Future): Add OCR
- Extract handwritten annotations
- Convert to searchable markdown
- Local OCR training support

### Phase 3 (Future): Bidirectional Sync
- Detect changes on both sides
- Three-way merge for conflicts
- Git-style conflict markers

## Contributing

1. Read the documentation in `docs/`
2. Check `CLAUDE.md` for development guidelines
3. Follow the task breakdown in `docs/TASKS.md`
4. Submit PRs with tests

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- [rmscene](https://github.com/ricklupton/rmscene) - reMarkable v6 file format library
- [mistune](https://github.com/lepture/mistune) - Markdown parser
- [watchdog](https://github.com/gorakhargosh/watchdog) - File system monitoring
- reMarkable community for format documentation
