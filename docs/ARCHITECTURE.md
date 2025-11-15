# Phase 1 Architecture: Obsidian → reMarkable Sync

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI Interface                             │
│                         (cli.py)                                 │
└───────────┬────────────────────┬────────────────────┬───────────┘
            │                    │                    │
            ▼                    ▼                    ▼
┌───────────────────┐  ┌─────────────────┐  ┌─────────────────────┐
│   Configuration   │  │   File Watcher  │  │   State Manager     │
│    (config.py)    │  │   (watcher.py)  │  │    (state.py)       │
└───────────────────┘  └────────┬────────┘  └──────────┬──────────┘
                                │                      │
                                ▼                      │
                       ┌─────────────────┐             │
                       │  Sync Engine    │◄────────────┘
                       │  (converter.py) │
                       └────────┬────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
           ┌─────────────────┐    ┌─────────────────────┐
           │ Markdown Parser │    │ reMarkable Generator │
           │   (parser.py)   │    │   (generator.py)     │
           └─────────────────┘    └──────────┬──────────┘
                                             │
                                             ▼
                                  ┌─────────────────────┐
                                  │ Metadata Generator  │
                                  │   (metadata.py)     │
                                  └─────────────────────┘
```

## Component Specifications

### 1. CLI Interface (`cli.py`)

**Responsibility**: Entry point for user commands, argument parsing, orchestration.

**Dependencies**: click, config, watcher, state, converter

**Key Functions**:

```python
@click.group()
@click.option('--config', '-c', default='~/.config/rm-obsidian-sync/config.toml')
@click.option('--verbose', '-v', is_flag=True)
def main(config: str, verbose: bool) -> None:
    """reMarkable-Obsidian Sync Tool"""
    pass

@main.command()
@click.option('--dry-run', is_flag=True, help='Preview without writing')
def sync(dry_run: bool) -> None:
    """Sync all changed files once"""
    # 1. Load config
    # 2. Initialize state manager
    # 3. Scan vault for changes
    # 4. Process each changed file
    # 5. Update state database

@main.command()
def watch() -> None:
    """Continuously monitor for changes"""
    # 1. Load config
    # 2. Start file watcher
    # 3. Run sync on detected changes
    # 4. Handle graceful shutdown (SIGINT, SIGTERM)

@main.command()
def status() -> None:
    """Show sync status"""
    # 1. Query state database
    # 2. Display synced/pending/error counts
    # 3. List recent activity
```

**Error Handling**:
- Invalid config: Exit with code 1, clear message
- Permission errors: Exit with code 2, suggest fix
- Runtime errors: Log to file, continue if possible

---

### 2. Configuration (`config.py`)

**Responsibility**: Load, validate, and provide configuration values.

**Dependencies**: tomllib (Python 3.11+) or tomli, pathlib

**Data Structure**:

```python
@dataclass(frozen=True)
class SyncConfig:
    obsidian_vault: Path
    remarkable_output: Path
    state_database: Path
    include_patterns: list[str]
    exclude_patterns: list[str]
    debounce_seconds: int
    
@dataclass(frozen=True)
class LayoutConfig:
    lines_per_page: int
    margin_top: int
    margin_bottom: int
    margin_left: int
    margin_right: int
    
@dataclass(frozen=True)
class AppConfig:
    sync: SyncConfig
    layout: LayoutConfig
    log_level: str
    log_file: Path

def load_config(config_path: Path) -> AppConfig:
    """Load and validate configuration from TOML file"""
    pass

def validate_paths(config: AppConfig) -> None:
    """Ensure all paths exist and are accessible"""
    pass
```

**Validation Rules**:
- Vault path must exist and be readable
- Output path must exist and be writable
- State database directory must be writable
- Numeric values must be positive
- Patterns must be valid glob syntax

---

### 3. File Watcher (`watcher.py`)

**Responsibility**: Monitor filesystem for changes, debounce events, trigger callbacks.

**Dependencies**: watchdog, threading

**Key Classes**:

```python
class ChangeHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[Path], None], debounce_ms: int):
        self.callback = callback
        self.debounce_ms = debounce_ms
        self.pending: dict[Path, float] = {}
        self.lock = threading.Lock()
        
    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith('.md'):
            self._queue_change(Path(event.src_path))
    
    def on_created(self, event: FileSystemEvent) -> None:
        # Same as on_modified
        pass
        
    def on_deleted(self, event: FileSystemEvent) -> None:
        # Mark for deletion in state database
        pass
    
    def _queue_change(self, path: Path) -> None:
        """Add to pending changes with current timestamp"""
        pass
    
    def process_pending(self) -> None:
        """Check for changes past debounce window, trigger callbacks"""
        pass

class VaultWatcher:
    def __init__(self, vault_path: Path, on_change: Callable[[Path], None], 
                 debounce_seconds: int = 5):
        self.observer = Observer()
        self.handler = ChangeHandler(on_change, debounce_seconds * 1000)
        
    def start(self) -> None:
        """Begin watching vault directory"""
        pass
        
    def stop(self) -> None:
        """Stop watching, cleanup resources"""
        pass
```

**Debounce Logic**:
1. On file event, record `(path, timestamp)` in pending dict
2. Every 1 second, check pending dict
3. If `current_time - timestamp > debounce_ms`, trigger callback
4. Remove from pending after callback

---

### 4. State Manager (`state.py`)

**Responsibility**: Persist sync state, track file mappings, detect changes.

**Dependencies**: sqlite3, pathlib, hashlib

**Database Schema**:

```sql
-- File sync state
CREATE TABLE sync_state (
    obsidian_path TEXT PRIMARY KEY,
    remarkable_uuid TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    last_sync_time INTEGER NOT NULL,
    page_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'synced'
);

-- Folder mappings
CREATE TABLE folder_mapping (
    obsidian_folder TEXT PRIMARY KEY,
    remarkable_uuid TEXT NOT NULL
);

-- Sync history for debugging
CREATE TABLE sync_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obsidian_path TEXT NOT NULL,
    action TEXT NOT NULL,  -- 'created', 'updated', 'deleted', 'error'
    timestamp INTEGER NOT NULL,
    details TEXT
);

-- Schema version for migrations
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY
);
```

**Key Functions**:

```python
class StateManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = self._connect()
        self._ensure_schema()
    
    def get_file_state(self, obsidian_path: str) -> Optional[SyncRecord]:
        """Get sync state for a file"""
        pass
    
    def update_file_state(self, record: SyncRecord) -> None:
        """Update or insert sync state"""
        pass
    
    def get_folder_uuid(self, folder_path: str) -> Optional[str]:
        """Get reMarkable UUID for Obsidian folder"""
        pass
    
    def create_folder_mapping(self, folder_path: str, uuid: str) -> None:
        """Store folder→UUID mapping"""
        pass
    
    def get_all_synced_files(self) -> list[SyncRecord]:
        """List all files in sync state"""
        pass
    
    def find_changed_files(self, vault_path: Path) -> list[Path]:
        """Compare vault to state, return files needing sync"""
        pass
    
    def compute_file_hash(self, file_path: Path) -> str:
        """SHA-256 hash of file content"""
        pass
    
    def log_sync_action(self, path: str, action: str, details: str = "") -> None:
        """Record sync action in history"""
        pass
```

**Transaction Safety**:
- All writes in transactions
- Rollback on any error
- WAL mode for concurrent reads
- Periodic checkpoint for durability

---

### 5. Markdown Parser (`parser.py`)

**Responsibility**: Parse markdown files into structured content blocks.

**Dependencies**: mistune (recommended) or markdown library

**Why Mistune**:
- Clean AST output (not just HTML)
- Extensible for custom elements
- Fast performance
- Active maintenance

**Key Functions**:

```python
def parse_markdown_file(file_path: Path) -> MarkdownDocument:
    """Parse markdown file into structured document"""
    content = file_path.read_text(encoding='utf-8')
    frontmatter, body = extract_frontmatter(content)
    blocks = parse_content(body)
    
    return MarkdownDocument(
        path=file_path,
        title=frontmatter.get('title', file_path.stem),
        content=blocks,
        frontmatter=frontmatter,
        last_modified=datetime.fromtimestamp(file_path.stat().st_mtime),
        content_hash=compute_hash(content)
    )

def extract_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML frontmatter from markdown content"""
    if content.startswith('---'):
        # Find closing ---
        # Parse YAML
        # Return (frontmatter_dict, remaining_content)
        pass
    return ({}, content)

def parse_content(markdown_text: str) -> list[ContentBlock]:
    """Convert markdown text to content blocks"""
    # Use mistune to get AST
    # Convert AST nodes to ContentBlock objects
    # Preserve inline formatting as TextFormat ranges
    pass

class MarkdownASTRenderer(mistune.AstRenderer):
    """Custom renderer to capture full AST structure"""
    
    def paragraph(self, children):
        # Convert paragraph node
        pass
    
    def heading(self, children, level):
        # Convert header node, preserve level
        pass
    
    def list(self, children, ordered, level, start=None):
        # Convert list, handle nesting
        pass
    
    def block_code(self, children, info=None):
        # Convert code block
        pass
    
    def emphasis(self, children):
        # Track italic formatting range
        pass
    
    def strong(self, children):
        # Track bold formatting range
        pass
```

**Inline Formatting Tracking**:

```python
def track_inline_formatting(ast_node) -> tuple[str, list[TextFormat]]:
    """
    Convert AST node to plain text while tracking formatting positions.
    
    Example:
        Input: "This is **bold** and *italic*"
        Output: ("This is bold and italic", [
            TextFormat(start=8, end=12, style=BOLD),
            TextFormat(start=17, end=23, style=ITALIC)
        ])
    """
    pass
```

**Edge Cases to Handle**:
- Nested formatting (`***bold and italic***`)
- Escaped characters (`\*not italic\*`)
- HTML in markdown (strip tags, keep text)
- Unicode characters (preserve as-is)
- Very long lines (soft wrap at word boundaries)

---

### 6. Sync Engine / Converter (`converter.py`)

**Responsibility**: Orchestrate the full conversion pipeline, handle multi-file sync.

**Dependencies**: parser, generator, metadata, state

**Key Functions**:

```python
class SyncEngine:
    def __init__(self, config: AppConfig, state: StateManager):
        self.config = config
        self.state = state
        self.generator = RemarkableGenerator(config.layout)
        
    def sync_file(self, markdown_path: Path) -> SyncResult:
        """
        Full pipeline for single file:
        1. Parse markdown
        2. Check if needs sync (hash comparison)
        3. Ensure parent folders exist in RM
        4. Generate RM document
        5. Write files to output directory
        6. Update state database
        """
        pass
    
    def sync_all_changed(self) -> list[SyncResult]:
        """Sync all files that have changed since last sync"""
        changed = self.state.find_changed_files(self.config.sync.obsidian_vault)
        results = []
        for path in changed:
            try:
                result = self.sync_file(path)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to sync {path}: {e}")
                results.append(SyncResult(path, success=False, error=str(e)))
        return results
    
    def ensure_folder_hierarchy(self, obsidian_path: Path) -> str:
        """
        Create reMarkable folders for each directory level.
        Returns UUID of immediate parent folder.
        """
        relative_path = obsidian_path.relative_to(self.config.sync.obsidian_vault)
        parent_uuid = ""  # Root level
        
        for part in relative_path.parent.parts:
            folder_path = str(Path(*relative_path.parent.parts[:relative_path.parent.parts.index(part)+1]))
            existing_uuid = self.state.get_folder_uuid(folder_path)
            
            if existing_uuid:
                parent_uuid = existing_uuid
            else:
                # Create new folder in RM
                new_uuid = str(uuid.uuid4())
                self._create_rm_folder(part, new_uuid, parent_uuid)
                self.state.create_folder_mapping(folder_path, new_uuid)
                parent_uuid = new_uuid
        
        return parent_uuid
    
    def _create_rm_folder(self, name: str, uuid: str, parent_uuid: str) -> None:
        """Create reMarkable folder (CollectionType) metadata"""
        pass

@dataclass
class SyncResult:
    path: Path
    success: bool
    remarkable_uuid: Optional[str] = None
    page_count: Optional[int] = None
    error: Optional[str] = None
```

**Pipeline Flow**:

```
markdown_path
    │
    ▼
parse_markdown_file() → MarkdownDocument
    │
    ▼
compute_hash() → compare with state
    │
    ▼ (if changed)
ensure_folder_hierarchy() → parent_uuid
    │
    ▼
generate_remarkable_document() → RemarkableDocument
    │
    ▼
write_rm_files() → files on disk
    │
    ▼
update_state() → database updated
    │
    ▼
SyncResult
```

---

### 7. reMarkable Generator (`generator.py`)

**Responsibility**: Convert parsed markdown into reMarkable v6 format files.

**Dependencies**: rmscene, uuid

**THIS IS THE MOST COMPLEX COMPONENT - Use sub-agent for implementation**

**Key Functions**:

```python
class RemarkableGenerator:
    def __init__(self, layout_config: LayoutConfig):
        self.layout = layout_config
        self.page_height = 1872  # pixels
        self.page_width = 1404   # pixels
        self.line_height = 30    # approximate pixels per line
        
    def generate_document(self, md_doc: MarkdownDocument, 
                          parent_uuid: str) -> RemarkableDocument:
        """
        Convert MarkdownDocument to RemarkableDocument.
        
        Steps:
        1. Generate document UUID
        2. Paginate content blocks
        3. Generate page UUIDs
        4. Create text items for each page
        5. Apply formatting
        """
        pass
    
    def paginate_content(self, blocks: list[ContentBlock]) -> list[list[ContentBlock]]:
        """
        Split content blocks into pages.
        
        Rules:
        - Target lines_per_page lines per page
        - Never split paragraph mid-way
        - Headers start new page if < 10 lines remain
        - Track running line count
        """
        pass
    
    def blocks_to_text_items(self, blocks: list[ContentBlock], 
                             page_index: int) -> list[TextItem]:
        """
        Convert content blocks to positioned text items.
        
        Each block becomes a TextItem with:
        - Calculated Y position based on preceding blocks
        - Applied indentation for lists
        - Formatting metadata
        """
        pass
    
    def generate_rm_file(self, page: RemarkablePage) -> bytes:
        """
        Generate binary .rm file content using rmscene.
        
        This is the critical integration point with rmscene.
        """
        # Use rmscene's write capabilities
        # Create scene tree with text items
        # Serialize to binary format
        pass
    
    def write_document_files(self, doc: RemarkableDocument, 
                             output_dir: Path) -> None:
        """
        Write all files for a reMarkable document:
        - {uuid}/ directory
        - {uuid}.metadata
        - {uuid}.content
        - {page_uuid}.rm for each page
        - {page_uuid}-metadata.json for each page
        """
        pass
```

**rmscene Integration Details**:

```python
# Example rmscene usage (based on library source)
from rmscene import scene_items, write_blocks, CrdtSequence

def create_text_item_scene(text: str, x: float, y: float, 
                            formatting: list[TextFormat]) -> bytes:
    """
    Create a scene tree with text using rmscene.
    
    This requires understanding rmscene's internal structure:
    - SceneTree contains items
    - TextItem contains CrdtSequence
    - CrdtSequence contains CrdtStr with actual text
    - Formatting applied via optional properties
    """
    # WARNING: rmscene's write API is experimental
    # May need to examine source code and tests
    # Consider contributing improvements upstream
    pass
```

**Sub-Agent Task for rmscene Integration**:

> **Context for Sub-Agent**: You need to implement reMarkable v6 file generation using the rmscene library. This requires deep understanding of:
> 
> 1. rmscene's SceneTree structure (scene_items.py)
> 2. How text is represented as CrdtSequence objects
> 3. The binary serialization format (write_blocks.py)
> 4. How formatting (bold, italic) is encoded
> 
> **Resources**:
> - rmscene GitHub: https://github.com/ricklupton/rmscene
> - PyPI page: https://pypi.org/project/rmscene/
> - Example code in rmc tool: https://github.com/ricklupton/rmc
> 
> **Approach**:
> 1. Install rmscene and explore its API
> 2. Read existing .rm files to understand structure
> 3. Study rmscene test cases for examples
> 4. Start with minimal text generation (single line)
> 5. Add formatting support
> 6. Handle multi-page documents
> 7. Document any library limitations or bugs found

---

### 8. Metadata Generator (`metadata.py`)

**Responsibility**: Generate reMarkable metadata files (`.metadata`, `.content`).

**Dependencies**: json, time

**Key Functions**:

```python
def generate_document_metadata(doc: RemarkableDocument) -> dict:
    """
    Generate .metadata file content.
    
    Returns dict suitable for JSON serialization.
    """
    return {
        "deleted": False,
        "lastModified": str(doc.modified_time),
        "lastOpened": "",
        "lastOpenedPage": 0,
        "metadatamodified": False,
        "modified": False,
        "parent": doc.parent_uuid,
        "pinned": False,
        "synced": True,
        "type": "DocumentType",
        "version": 1,
        "visibleName": doc.visible_name
    }

def generate_content_metadata(doc: RemarkableDocument) -> dict:
    """
    Generate .content file content.
    
    Includes page list and tool settings.
    """
    return {
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
        "pageCount": len(doc.pages),
        "pages": [page.uuid for page in doc.pages],
        "textAlignment": "left",
        "textScale": 1
    }

def generate_page_metadata(page: RemarkablePage) -> dict:
    """
    Generate {page_uuid}-metadata.json content.
    
    Page-specific settings.
    """
    return {
        "layers": [
            {
                "name": "Layer 1",
                "visible": True
            }
        ]
    }

def generate_folder_metadata(name: str, parent_uuid: str, 
                             timestamp: int) -> dict:
    """
    Generate .metadata for folder (CollectionType).
    """
    return {
        "deleted": False,
        "lastModified": str(timestamp),
        "lastOpened": "",
        "metadatamodified": False,
        "modified": False,
        "parent": parent_uuid,
        "pinned": False,
        "synced": True,
        "type": "CollectionType",
        "version": 1,
        "visibleName": name
    }

def current_rm_timestamp() -> int:
    """Get current time as 13-digit Unix timestamp (milliseconds)"""
    return int(time.time() * 1000)
```

---

## Data Flow Examples

### Example 1: Simple Markdown File

**Input** (`notes/meeting.md`):
```markdown
---
title: Team Meeting Notes
---

# Q4 Planning

We discussed the **quarterly goals** and *timeline*.

## Action Items

- Complete design review
- Update documentation
- Schedule follow-up
```

**Parser Output** (MarkdownDocument):
```python
MarkdownDocument(
    path=Path("notes/meeting.md"),
    title="Team Meeting Notes",
    content=[
        ContentBlock(type=HEADER, level=1, text="Q4 Planning", formatting=[]),
        ContentBlock(type=PARAGRAPH, text="We discussed the quarterly goals and timeline.", 
                    formatting=[
                        TextFormat(start=16, end=31, style=BOLD),
                        TextFormat(start=36, end=44, style=ITALIC)
                    ]),
        ContentBlock(type=HEADER, level=2, text="Action Items", formatting=[]),
        ContentBlock(type=LIST_ITEM, level=1, text="Complete design review", formatting=[]),
        ContentBlock(type=LIST_ITEM, level=1, text="Update documentation", formatting=[]),
        ContentBlock(type=LIST_ITEM, level=1, text="Schedule follow-up", formatting=[]),
    ],
    frontmatter={"title": "Team Meeting Notes"},
    ...
)
```

**Generator Output** (RemarkableDocument):
```python
RemarkableDocument(
    uuid="550e8400-e29b-41d4-a716-446655440000",
    visible_name="Team Meeting Notes",
    parent_uuid="folder-uuid-for-notes",
    pages=[
        RemarkablePage(
            uuid="page-1-uuid",
            text_items=[
                TextItem(text="Q4 Planning", x=50, y=50, width=1304, 
                        formatting=[]),  # Header styling applied differently
                TextItem(text="We discussed the quarterly goals and timeline.", 
                        x=50, y=120, width=1304,
                        formatting=[
                            TextFormat(start=16, end=31, style=BOLD),
                            TextFormat(start=36, end=44, style=ITALIC)
                        ]),
                TextItem(text="Action Items", x=50, y=200, width=1304, formatting=[]),
                TextItem(text="• Complete design review", x=70, y=270, width=1284, formatting=[]),
                TextItem(text="• Update documentation", x=70, y=310, width=1284, formatting=[]),
                TextItem(text="• Schedule follow-up", x=70, y=350, width=1284, formatting=[]),
            ]
        )
    ],
    created_time=1700000000000,
    modified_time=1700000000000
)
```

### Example 2: Multi-Page Document

For a 150-line markdown file:
- Parser produces single MarkdownDocument with all content
- Generator's `paginate_content()` splits into ~3-4 pages
- Each page gets own UUID and `.rm` file
- All pages listed in `.content` file's pages array

---

## Error Handling Architecture

### Logging Strategy

```python
import logging

# Configure hierarchical loggers
logger = logging.getLogger('rm_obsidian_sync')
parser_logger = logging.getLogger('rm_obsidian_sync.parser')
generator_logger = logging.getLogger('rm_obsidian_sync.generator')
state_logger = logging.getLogger('rm_obsidian_sync.state')

# Log levels by component:
# - CLI: INFO (user-facing progress)
# - Watcher: DEBUG (filesystem events)
# - Parser: WARNING (malformed markdown)
# - Generator: ERROR (rmscene failures)
# - State: INFO (sync actions)

def setup_logging(log_level: str, log_file: Path) -> None:
    """Configure logging with file and console handlers"""
    pass
```

### Exception Hierarchy

```python
class SyncError(Exception):
    """Base exception for sync errors"""
    pass

class ConfigError(SyncError):
    """Configuration is invalid or missing"""
    pass

class ParseError(SyncError):
    """Failed to parse markdown file"""
    pass

class GenerationError(SyncError):
    """Failed to generate reMarkable files"""
    pass

class StateError(SyncError):
    """Database or state management error"""
    pass

class IOError(SyncError):
    """File system operation failed"""
    pass
```

### Recovery Patterns

**Retry with backoff**:
```python
def retry_with_backoff(func, max_attempts=4, initial_delay=1):
    """Retry function with exponential backoff"""
    for attempt in range(max_attempts):
        try:
            return func()
        except Exception as e:
            if attempt == max_attempts - 1:
                raise
            delay = initial_delay * (2 ** attempt)
            logger.warning(f"Attempt {attempt+1} failed, retrying in {delay}s: {e}")
            time.sleep(delay)
```

**Atomic file writes**:
```python
def atomic_write(path: Path, content: bytes) -> None:
    """Write content atomically using temp file + rename"""
    temp_path = path.with_suffix('.tmp')
    try:
        temp_path.write_bytes(content)
        temp_path.replace(path)  # Atomic on POSIX
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
```

---

## Testing Architecture

### Unit Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── test_parser.py           # Markdown parsing tests
├── test_generator.py        # RM generation tests
├── test_metadata.py         # Metadata file tests
├── test_state.py            # Database operations tests
├── test_converter.py        # Integration pipeline tests
├── test_watcher.py          # File watching tests
└── fixtures/
    └── sample_markdown/
        ├── simple.md        # Basic paragraph
        ├── headers.md       # H1-H6 headers
        ├── formatting.md    # Bold, italic, code
        ├── lists.md         # Nested lists
        ├── frontmatter.md   # YAML metadata
        ├── long.md          # 100+ pages
        └── edge_cases.md    # Empty, minimal, weird
```

### Test Fixtures

```python
# conftest.py
import pytest
from pathlib import Path

@pytest.fixture
def sample_markdown_dir():
    return Path(__file__).parent / "fixtures" / "sample_markdown"

@pytest.fixture
def simple_markdown(sample_markdown_dir):
    return (sample_markdown_dir / "simple.md").read_text()

@pytest.fixture
def temp_output_dir(tmp_path):
    output = tmp_path / "remarkable_output"
    output.mkdir()
    return output

@pytest.fixture
def state_manager(tmp_path):
    db_path = tmp_path / "state.db"
    return StateManager(db_path)
```

### Testing rmscene Output

```python
def test_generated_rm_file_is_valid(generator, sample_content):
    """Generated .rm file should be parseable by rmscene"""
    rm_bytes = generator.generate_rm_file(sample_content)
    
    # Round-trip test: write then read back
    from rmscene import read_blocks
    blocks = list(read_blocks(io.BytesIO(rm_bytes)))
    
    # Verify structure
    assert len(blocks) > 0
    # Check for expected block types
    # Verify text content preserved
```

---

## Deployment Considerations

### Package Structure (pyproject.toml)

```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "rm-obsidian-sync"
version = "0.1.0"
description = "Sync Obsidian markdown to reMarkable Paper Pro"
requires-python = ">=3.10"
dependencies = [
    "rmscene>=0.7.0",
    "watchdog>=3.0.0",
    "mistune>=3.0.0",
    "click>=8.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
    "black>=23.0.0",
    "mypy>=1.0.0",
    "ruff>=0.1.0",
]

[project.scripts]
rm-obsidian-sync = "rm_obsidian_sync.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

### System Service (systemd)

```ini
# /etc/systemd/user/rm-obsidian-sync.service
[Unit]
Description=reMarkable Obsidian Sync
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/rm-obsidian-sync watch
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

### Resource Usage Targets

- Memory: < 100MB for typical vault
- CPU: < 1% idle, < 50% during sync
- Disk I/O: Burst during sync, minimal otherwise
- File handles: < 100 (watcher + database)
