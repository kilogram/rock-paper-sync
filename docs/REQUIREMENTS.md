# Phase 1 Requirements: Obsidian → reMarkable Sync

## Functional Requirements

### FR-1: Markdown File Detection
- **FR-1.1**: Monitor specified Obsidian vault directory for `.md` file changes
- **FR-1.2**: Detect file creation, modification, and deletion events
- **FR-1.3**: Ignore non-markdown files (images, PDFs, etc.)
- **FR-1.4**: Respect `.gitignore` patterns if present
- **FR-1.5**: Debounce rapid file changes (wait 5 seconds after last change)

### FR-2: Markdown Parsing
- **FR-2.1**: Parse standard CommonMark markdown syntax
- **FR-2.2**: Extract document structure (headers, paragraphs, lists)
- **FR-2.3**: Preserve inline formatting (bold, italic, code)
- **FR-2.4**: Handle YAML frontmatter (extract title, ignore other fields)
- **FR-2.5**: Support nested lists up to 3 levels deep
- **FR-2.6**: Convert links to plain text with URL in parentheses
- **FR-2.7**: Convert images to placeholder text `[Image: filename]`
- **FR-2.8**: Handle code blocks as monospace text blocks

### FR-3: reMarkable Document Generation
- **FR-3.1**: Generate valid v6 format `.rm` files readable by device
- **FR-3.2**: Create text fields using CRDT sequences (typed text, not strokes)
- **FR-3.3**: Apply text formatting: bold via `font-weight: bold`, italic via `font-style: italic`
- **FR-3.4**: Generate unique UUID for each document and page
- **FR-3.5**: Create proper metadata files (`.metadata`, `.content`)
- **FR-3.6**: Set appropriate timestamps (13-digit millisecond Unix time)
- **FR-3.7**: Support documents up to 100 pages

### FR-4: Page Layout
- **FR-4.1**: Target ~45 lines per page at standard font size
- **FR-4.2**: Break pages at paragraph boundaries (never mid-paragraph)
- **FR-4.3**: Headers (H1, H2) start new page if less than 10 lines remain
- **FR-4.4**: Preserve indentation for list items and nested content
- **FR-4.5**: Add page margins (configurable, default 50px on all sides)
- **FR-4.6**: Line spacing consistent with reMarkable's native rendering

### FR-5: Folder Hierarchy Preservation
- **FR-5.1**: Map Obsidian folder structure to reMarkable parent relationships
- **FR-5.2**: Create reMarkable folders (CollectionType) for each Obsidian directory
- **FR-5.3**: Nested folders maintain parent UUID references
- **FR-5.4**: Root-level files have empty parent string
- **FR-5.5**: Folder names match Obsidian directory names

### FR-6: State Management
- **FR-6.1**: Track which files have been synced (path → UUID mapping)
- **FR-6.2**: Store content hash to detect actual changes vs. timestamp updates
- **FR-6.3**: Persist state across application restarts (SQLite database)
- **FR-6.4**: Support incremental sync (only process changed files)
- **FR-6.5**: Handle file renames by detecting content similarity

### FR-7: Configuration
- **FR-7.1**: Specify Obsidian vault path
- **FR-7.2**: Specify reMarkable output directory (Syncthing sync folder)
- **FR-7.3**: Configure included/excluded paths (glob patterns)
- **FR-7.4**: Set page layout preferences (margins, lines per page)
- **FR-7.5**: Configuration via TOML file and CLI overrides

### FR-8: Command-Line Interface
- **FR-8.1**: `sync` command - Run one-time sync of all changed files
- **FR-8.2**: `watch` command - Continuously monitor for changes
- **FR-8.3**: `status` command - Show sync state and pending changes
- **FR-8.4**: `reset` command - Clear sync state (force full re-sync)
- **FR-8.5**: `--verbose` flag for detailed logging
- **FR-8.6**: `--dry-run` flag to preview changes without writing

## Non-Functional Requirements

### NFR-1: Reliability
- **NFR-1.1**: Never corrupt or lose user's original markdown files
- **NFR-1.2**: Atomic file operations (no partial writes on failure)
- **NFR-1.3**: Graceful handling of malformed markdown (skip, log, continue)
- **NFR-1.4**: Resume after application crash without duplicate processing
- **NFR-1.5**: Handle filesystem full errors without data corruption

### NFR-2: Performance
- **NFR-2.1**: Process typical markdown file (<10KB) in under 1 second
- **NFR-2.2**: Handle vaults with 1000+ files without excessive memory usage
- **NFR-2.3**: File watcher overhead < 1% CPU when idle
- **NFR-2.4**: Initial full sync of 100 files completes in under 2 minutes
- **NFR-2.5**: State database queries return in < 10ms

### NFR-3: Usability
- **NFR-3.1**: Clear error messages explaining what went wrong and how to fix
- **NFR-3.2**: Progress indication for long operations
- **NFR-3.3**: Configurable log verbosity (debug, info, warning, error)
- **NFR-3.4**: Documentation for all CLI commands
- **NFR-3.5**: Example configuration file with comments

### NFR-4: Maintainability
- **NFR-4.1**: Modular architecture with clear separation of concerns
- **NFR-4.2**: Comprehensive unit test coverage (>80%)
- **NFR-4.3**: Type hints throughout codebase
- **NFR-4.4**: Docstrings for all public functions and classes
- **NFR-4.5**: Logging at key decision points for debugging

### NFR-5: Compatibility
- **NFR-5.1**: Python 3.10+ support
- **NFR-5.2**: Cross-platform (Linux, macOS, Windows)
- **NFR-5.3**: reMarkable firmware 3.0+ (v6 format)
- **NFR-5.4**: Forward compatibility with unknown rmscene block types

## Data Models

### Markdown Document Model

```python
@dataclass
class MarkdownDocument:
    path: Path                    # Original file path
    title: str                    # From frontmatter or filename
    content: list[ContentBlock]   # Parsed content blocks
    frontmatter: dict[str, Any]   # YAML frontmatter data
    last_modified: datetime       # File modification time
    content_hash: str             # SHA-256 of content

@dataclass
class ContentBlock:
    type: BlockType               # paragraph, header, list, code, etc.
    level: int                    # Header level (1-6) or list depth
    text: str                     # Plain text content
    formatting: list[TextFormat]  # Inline formatting ranges
    children: list[ContentBlock]  # Nested content (for lists)

@dataclass
class TextFormat:
    start: int                    # Character offset
    end: int                      # Character offset
    style: FormatStyle            # bold, italic, code, link
    metadata: dict[str, str]      # Additional info (URL for links)

class BlockType(Enum):
    PARAGRAPH = "paragraph"
    HEADER = "header"
    LIST_ITEM = "list_item"
    CODE_BLOCK = "code"
    BLOCKQUOTE = "blockquote"
    HORIZONTAL_RULE = "hr"

class FormatStyle(Enum):
    BOLD = "bold"
    ITALIC = "italic"
    CODE = "code"
    LINK = "link"
    STRIKETHROUGH = "strikethrough"
```

### reMarkable Document Model

```python
@dataclass
class RemarkableDocument:
    uuid: str                     # Document UUID
    visible_name: str             # Display name
    parent_uuid: str              # Parent folder UUID (empty for root)
    pages: list[RemarkablePage]   # Page content
    created_time: int             # 13-digit timestamp
    modified_time: int            # 13-digit timestamp

@dataclass
class RemarkablePage:
    uuid: str                     # Page UUID
    text_items: list[TextItem]    # Text blocks on page
    
@dataclass
class TextItem:
    text: str                     # Plain text content
    x: float                      # X position (pixels)
    y: float                      # Y position (pixels)
    width: float                  # Text box width
    formatting: list[TextFormat]  # Applied formatting
```

### Sync State Model

```python
@dataclass
class SyncRecord:
    obsidian_path: str            # Relative path in vault
    remarkable_uuid: str          # Document UUID
    content_hash: str             # SHA-256 of markdown content
    last_sync_time: int           # Unix timestamp
    page_count: int               # Number of RM pages
    status: SyncStatus            # synced, pending, error

class SyncStatus(Enum):
    SYNCED = "synced"             # Up to date
    PENDING = "pending"           # Needs sync
    ERROR = "error"               # Failed to sync
    DELETED = "deleted"           # Removed from Obsidian
```

## File Format Specifications

### reMarkable .metadata File

```json
{
  "deleted": false,
  "lastModified": "1700000000000",
  "lastOpened": "1700000000000",
  "lastOpenedPage": 0,
  "metadatamodified": false,
  "modified": false,
  "parent": "",
  "pinned": false,
  "synced": true,
  "type": "DocumentType",
  "version": 1,
  "visibleName": "Document Title"
}
```

### reMarkable .content File

```json
{
  "coverPageNumber": 0,
  "documentMetadata": {},
  "extraMetadata": {
    "LastBrushColor": "Black",
    "LastBrushThicknessScale": "2",
    "LastColor": "Black",
    "LastEraserThicknessScale": "2",
    "LastEraserTool": "Eraser",
    "LastPen": "Ballpointv2",
    "LastPenColor": "Black",
    "LastPenThicknessScale": "2",
    "LastReplacementColor": "Black",
    "LastTool": "Ballpointv2"
  },
  "fileType": "notebook",
  "fontName": "",
  "lineHeight": -1,
  "margins": 100,
  "orientation": "portrait",
  "pageCount": 1,
  "pages": [
    "page-uuid-here"
  ],
  "textAlignment": "left",
  "textScale": 1
}
```

### Configuration File (config.toml)

```toml
[paths]
obsidian_vault = "/home/user/obsidian-vault"
remarkable_output = "/home/user/remarkable-sync"
state_database = "~/.local/share/rm-obsidian-sync/state.db"

[sync]
include_patterns = ["**/*.md"]
exclude_patterns = [".obsidian/**", "templates/**"]
debounce_seconds = 5

[layout]
lines_per_page = 45
margin_top = 50
margin_bottom = 50
margin_left = 50
margin_right = 50

[logging]
level = "info"
file = "~/.local/share/rm-obsidian-sync/sync.log"
```

## Error Handling Specifications

### Error Categories

1. **Configuration Errors** (fatal, exit immediately)
   - Missing required config file
   - Invalid paths (directory doesn't exist)
   - Permission denied on directories

2. **Parse Errors** (skip file, log warning, continue)
   - Malformed markdown syntax
   - Invalid YAML frontmatter
   - Encoding errors (non-UTF8)

3. **Generation Errors** (retry once, then skip)
   - rmscene library errors
   - Invalid text content (control characters)
   - Excessive page count (>100 pages)

4. **I/O Errors** (retry with backoff, then fail)
   - Disk full
   - Permission denied on output
   - File locked by another process

5. **State Database Errors** (critical, require manual intervention)
   - Database corruption
   - Schema migration failures
   - Concurrent access conflicts

### Error Recovery Strategies

- **Atomic writes**: Write to temp file, rename on success
- **Backup before modify**: Keep `.bak` of previous version
- **Transaction rollback**: Database operations in transactions
- **Retry with exponential backoff**: 1s, 2s, 4s, 8s, fail
- **Quarantine problematic files**: Move to `.sync-errors/` subdirectory

## Testing Requirements

### Unit Tests

- Parser: Each markdown element type (headers, lists, formatting)
- Converter: Content block to RM text item conversion
- Generator: Valid v6 file structure creation
- Metadata: Correct JSON structure and timestamps
- State: Database operations (CRUD, queries)

### Integration Tests

- Full pipeline: Markdown file → reMarkable document
- File watcher: Detect changes, trigger processing
- Error handling: Graceful failure scenarios
- Configuration: Load and validate config file

### Fixture Requirements

Create sample markdown files covering:
- Simple paragraph text
- Multiple header levels (H1-H6)
- Nested lists (bullets, numbers, mixed)
- Inline formatting (bold, italic, combined)
- Code blocks (fenced and indented)
- Links and images
- YAML frontmatter
- Very long documents (100+ pages)
- Edge cases (empty file, only headers, no content)

### Manual Testing Checklist

- [ ] Generated files load on reMarkable device
- [ ] Text is readable at default zoom
- [ ] Formatting (bold/italic) renders correctly
- [ ] Multi-page documents paginate properly
- [ ] Folder structure matches Obsidian
- [ ] File modifications trigger re-sync
- [ ] Large vault sync completes successfully
