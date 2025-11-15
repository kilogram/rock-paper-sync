# reMarkable-Obsidian Sync Tool - Phase 1

## Project Overview

Build a one-way synchronization tool that converts Obsidian markdown files into reMarkable Paper Pro documents. The tool monitors an Obsidian vault directory, converts markdown to reMarkable's v6 format with text fields, and outputs to a Syncthing-synced directory for automatic device propagation.

## Key Constraints

- **Language**: Python 3.10+
- **Core Library**: rmscene (v0.7.0+) for reMarkable file generation
- **Target Format**: reMarkable v6 (firmware 3.0+)
- **Sync Direction**: Obsidian → reMarkable only (Phase 1)
- **No External Services**: All processing is local

## Architecture Summary

```
[Obsidian Vault] → [File Watcher] → [Markdown Parser] → [RM Generator] → [Syncthing Dir]
     ↓                    ↓                 ↓                  ↓                ↓
  .md files          watchdog          mistune/md        rmscene          UUID dirs
```

## Directory Structure

```
remarkable-obsidian-sync/
├── CLAUDE.md                    # This file
├── docs/                        # Project documentation
│   ├── REQUIREMENTS.md          # Detailed requirements
│   ├── ARCHITECTURE.md          # Technical architecture
│   └── TASKS.md                 # Implementation tasks
├── src/
│   └── rm_obsidian_sync/
│       ├── __init__.py
│       ├── cli.py               # Command-line interface
│       ├── config.py            # Configuration management
│       ├── watcher.py           # File system monitoring
│       ├── parser.py            # Markdown parsing
│       ├── converter.py         # MD → RM conversion
│       ├── generator.py         # RM file generation
│       ├── metadata.py          # RM metadata handling
│       └── state.py             # Sync state database
├── tests/
│   ├── conftest.py
│   ├── test_parser.py
│   ├── test_converter.py
│   ├── test_generator.py
│   └── fixtures/
│       └── sample_markdown/
├── pyproject.toml
└── README.md
```

## Implementation Strategy

### Use Sub-Agents for Complex Tasks

When implementing complex components, spawn sub-agents with focused contexts:

1. **RM Format Sub-Agent**: Focus on rmscene library usage, v6 format structure
2. **Markdown Parser Sub-Agent**: Handle markdown parsing edge cases
3. **State Management Sub-Agent**: SQLite schema and sync state logic
4. **Test Suite Sub-Agent**: Comprehensive test coverage

### Development Order

1. **Foundation** (do first):
   - Project setup (pyproject.toml, dependencies)
   - Configuration system
   - Logging infrastructure

2. **Core Pipeline** (main implementation):
   - Markdown parser
   - reMarkable file generator (most complex)
   - Metadata generator
   - Format converter (orchestrates parser → generator)

3. **Integration** (connect components):
   - File watcher
   - State database
   - CLI interface

4. **Testing & Polish**:
   - Unit tests for each component
   - Integration tests for full pipeline
   - Error handling and recovery

## Critical Implementation Notes

### reMarkable File Structure

Each document requires these files in `{uuid}/`:
- `{uuid}.metadata` - JSON with visibleName, type, parent, lastModified
- `{uuid}.content` - JSON with pages array, pageCount, tool settings
- `{page-uuid}.rm` - Binary v6 format with text/strokes per page
- `{page-uuid}-metadata.json` - Page-specific settings

### UUID Management

- Generate UUID4 for each document and page
- Maintain mapping: `obsidian_path → remarkable_uuid`
- Store in SQLite for persistence across runs

### Text Field Generation

rmscene's experimental writer creates CrdtSequence structures:
```python
from rmscene import scene_items, write_blocks

# Text is stored as CRDT sequences in RootTextBlock
# Each paragraph becomes a text item in the scene tree
```

### Page Breaking Logic

- Estimate ~45-50 lines per page at default font
- Break at paragraph boundaries (double newline)
- Never break mid-sentence if avoidable
- Headers start new pages if near bottom

### Metadata Timestamps

reMarkable uses 13-digit Unix timestamps (milliseconds):
```python
import time
timestamp = int(time.time() * 1000)  # 13 digits
```

## Dependencies

```toml
[project]
dependencies = [
    "rmscene>=0.7.0",
    "watchdog>=3.0.0",
    "mistune>=3.0.0",
    "click>=8.0.0",
]
```

## Testing Approach

1. **Unit tests** for each module (parser, generator, etc.)
2. **Fixture-based** testing with sample markdown files
3. **Round-trip validation**: Generate RM file, parse it back, verify content
4. **Manual device testing**: Load generated files on actual reMarkable

## Error Handling Priorities

1. **Never lose user data** - Always preserve original markdown
2. **Fail gracefully** - Log errors, skip problematic files, continue
3. **Atomic operations** - Don't leave partial files on failure
4. **Clear error messages** - Help user understand what went wrong

## Key Files to Reference

Before implementing any component, read:
- `docs/REQUIREMENTS.md` - What exactly to build
- `docs/ARCHITECTURE.md` - How components interact
- `docs/TASKS.md` - Specific implementation tasks with acceptance criteria

## Sub-Agent Spawning Pattern

When you need deep focus on a complex area:

```
Task: Implement reMarkable file generator using rmscene

Sub-agent context:
- Read rmscene source code on GitHub
- Focus on scene_items.py and write_blocks.py
- Understand CrdtSequence structure
- Test with minimal examples first
- Document any rmscene bugs or limitations found
```

This keeps main context focused on orchestration while sub-agents dive deep.

## Success Criteria for Phase 1

- [ ] Convert markdown to readable reMarkable documents
- [ ] Preserve basic formatting (headers, bold, italic)
- [ ] Handle multi-page documents correctly
- [ ] Maintain folder hierarchy through metadata
- [ ] Watch directory for changes and auto-convert
- [ ] State database tracks what's been synced
- [ ] Comprehensive error logging
- [ ] Test coverage >80%

## Getting Started

1. Read all docs in `docs/` directory
2. Set up project skeleton with pyproject.toml
3. Implement components in order specified in TASKS.md
4. Test each component before moving to next
5. Integration test the full pipeline
6. Manual testing on actual reMarkable device
