# Phase 1 Implementation Tasks

## Overview

This document breaks down the Phase 1 implementation into discrete tasks with clear acceptance criteria. Tasks are ordered for optimal development flow, with dependencies explicitly noted.

**Estimated Total Time**: 4-6 weeks for production quality

---

## Task 1: Project Setup

**Priority**: Critical (must complete first)  
**Estimated Time**: 2-4 hours  
**Dependencies**: None

### 1.1 Create pyproject.toml

```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "rock-paper-sync"
version = "0.1.0"
description = "Sync Obsidian markdown to reMarkable Paper Pro"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}
authors = [
    {name = "Your Name", email = "your@email.com"}
]
dependencies = [
    "rmscene>=0.7.0",
    "watchdog>=3.0.0",
    "mistune>=3.0.0",
    "click>=8.0.0",
    "pyyaml>=6.0.0",
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
rock-paper-sync = "rock_paper_sync.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.black]
line-length = 100

[tool.mypy]
python_version = "3.10"
strict = true

[tool.ruff]
line-length = 100
select = ["E", "F", "I", "N", "W"]
```

### 1.2 Create Directory Structure

```bash
mkdir -p src/rock_paper_sync tests/fixtures/sample_markdown
touch src/rock_paper_sync/__init__.py
touch src/rock_paper_sync/py.typed  # PEP 561 marker
```

### 1.3 Create Basic README.md

Document project purpose, installation, and basic usage.

### 1.4 Initialize Git Repository

```bash
git init
echo "__pycache__/" >> .gitignore
echo "*.egg-info/" >> .gitignore
echo ".venv/" >> .gitignore
echo "*.db" >> .gitignore
```

### Acceptance Criteria
- [ ] `pip install -e .` succeeds
- [ ] `pip install -e ".[dev]"` installs dev dependencies
- [ ] `rock-paper-sync --help` shows usage (after CLI implemented)
- [ ] All type hints pass mypy strict mode
- [ ] Code formatted with black

---

## Task 2: Configuration System

**Priority**: High  
**Estimated Time**: 4-6 hours  
**Dependencies**: Task 1

### 2.1 Implement config.py

Create configuration loading and validation:

```python
# src/rock_paper_sync/config.py
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib  # Python 3.11+, or use tomli for 3.10

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
    """Load configuration from TOML file"""
    # Implementation here
    pass

def validate_config(config: AppConfig) -> None:
    """Validate paths exist and are accessible"""
    # Check vault exists
    # Check output is writable
    # Check numeric values are positive
    pass

def expand_path(path_str: str) -> Path:
    """Expand ~ and environment variables in path"""
    pass
```

### 2.2 Create Default Config Template

Create `example_config.toml`:

```toml
# reMarkable-Obsidian Sync Configuration

[paths]
# Path to your Obsidian vault
obsidian_vault = "~/obsidian-vault"

# Path where reMarkable files will be written
# This should be your Syncthing sync folder
remarkable_output = "~/remarkable-sync"

# Path to sync state database
state_database = "~/.local/share/rock-paper-sync/state.db"

[sync]
# Which files to include (glob patterns)
include_patterns = ["**/*.md"]

# Which files to exclude (glob patterns)
exclude_patterns = [
    ".obsidian/**",
    "templates/**",
    ".git/**",
    "*.excalidraw.md"
]

# Seconds to wait after file change before syncing
debounce_seconds = 5

[layout]
# Approximate lines per page (affects pagination)
lines_per_page = 45

# Page margins in pixels (reMarkable resolution: 1404x1872)
margin_top = 50
margin_bottom = 50
margin_left = 50
margin_right = 50

[logging]
# Log level: debug, info, warning, error
level = "info"

# Log file path
file = "~/.local/share/rock-paper-sync/sync.log"
```

### 2.3 Write Config Tests

Test cases:
- Load valid config file
- Missing required field raises error
- Invalid path raises error
- Path expansion works for ~
- Default values applied correctly

### Acceptance Criteria
- [ ] Loads TOML config file successfully
- [ ] Validates all required fields present
- [ ] Expands ~ in paths correctly
- [ ] Raises clear error for missing vault directory
- [ ] Raises clear error for unwritable output directory
- [ ] Frozen dataclasses prevent accidental modification
- [ ] 100% test coverage for config module

---

## Task 3: Logging Infrastructure

**Priority**: High  
**Estimated Time**: 2-3 hours  
**Dependencies**: Task 2

### 3.1 Setup Hierarchical Logging

```python
# src/rock_paper_sync/logging_setup.py
import logging
from pathlib import Path

def setup_logging(log_level: str, log_file: Path) -> None:
    """Configure application logging"""
    
    # Create log directory if needed
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Configure root logger
    root_logger = logging.getLogger('rock_paper_sync')
    root_logger.setLevel(logging.DEBUG)  # Capture all, filter at handler level
    
    # Console handler (user-facing)
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, log_level.upper()))
    console_format = logging.Formatter('%(levelname)s: %(message)s')
    console.setFormatter(console_format)
    
    # File handler (detailed debugging)
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        '%(asctime)s %(name)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_format)
    
    root_logger.addHandler(console)
    root_logger.addHandler(file_handler)
```

### 3.2 Create Component Loggers

Each module gets its own logger:

```python
# In each module
import logging
logger = logging.getLogger('rock_paper_sync.parser')  # or .generator, .state, etc.
```

### Acceptance Criteria
- [ ] Console shows INFO level by default
- [ ] File captures DEBUG level details
- [ ] Log file created automatically
- [ ] Each component logs to its namespace
- [ ] Timestamps in ISO format in file
- [ ] No duplicate log entries

---

## Task 4: State Database

**Priority**: High  
**Estimated Time**: 6-8 hours  
**Dependencies**: Task 2

### 4.1 Implement state.py

**Sub-agent recommended for schema design and migration handling.**

```python
# src/rock_paper_sync/state.py
import sqlite3
import hashlib
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger('rock_paper_sync.state')

@dataclass
class SyncRecord:
    obsidian_path: str
    remarkable_uuid: str
    content_hash: str
    last_sync_time: int
    page_count: int
    status: str  # 'synced', 'pending', 'error'

class StateManager:
    SCHEMA_VERSION = 1
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()
    
    def _ensure_schema(self) -> None:
        """Create tables if they don't exist"""
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    obsidian_path TEXT PRIMARY KEY,
                    remarkable_uuid TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    last_sync_time INTEGER NOT NULL,
                    page_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'synced'
                );
                
                CREATE TABLE IF NOT EXISTS folder_mapping (
                    obsidian_folder TEXT PRIMARY KEY,
                    remarkable_uuid TEXT NOT NULL
                );
                
                CREATE TABLE IF NOT EXISTS sync_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    obsidian_path TEXT NOT NULL,
                    action TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    details TEXT
                );
                
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );
                
                INSERT OR IGNORE INTO schema_version (version) VALUES (1);
            """)
    
    def get_file_state(self, obsidian_path: str) -> Optional[SyncRecord]:
        """Get sync state for a file"""
        cursor = self.conn.execute(
            "SELECT * FROM sync_state WHERE obsidian_path = ?",
            (obsidian_path,)
        )
        row = cursor.fetchone()
        if row:
            return SyncRecord(**dict(row))
        return None
    
    def update_file_state(self, record: SyncRecord) -> None:
        """Insert or update sync state"""
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO sync_state 
                (obsidian_path, remarkable_uuid, content_hash, last_sync_time, page_count, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (record.obsidian_path, record.remarkable_uuid, record.content_hash,
                  record.last_sync_time, record.page_count, record.status))
    
    def get_folder_uuid(self, folder_path: str) -> Optional[str]:
        """Get reMarkable UUID for Obsidian folder"""
        cursor = self.conn.execute(
            "SELECT remarkable_uuid FROM folder_mapping WHERE obsidian_folder = ?",
            (folder_path,)
        )
        row = cursor.fetchone()
        return row[0] if row else None
    
    def create_folder_mapping(self, folder_path: str, uuid: str) -> None:
        """Store folder→UUID mapping"""
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO folder_mapping (obsidian_folder, remarkable_uuid) VALUES (?, ?)",
                (folder_path, uuid)
            )
    
    def compute_file_hash(self, file_path: Path) -> str:
        """SHA-256 hash of file content"""
        hasher = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    def find_changed_files(self, vault_path: Path, include_patterns: list[str],
                           exclude_patterns: list[str]) -> list[Path]:
        """Find files that need syncing"""
        changed = []
        
        for pattern in include_patterns:
            for file_path in vault_path.glob(pattern):
                # Check exclusions
                if self._is_excluded(file_path, vault_path, exclude_patterns):
                    continue
                
                relative_path = str(file_path.relative_to(vault_path))
                current_hash = self.compute_file_hash(file_path)
                
                state = self.get_file_state(relative_path)
                if state is None or state.content_hash != current_hash:
                    changed.append(file_path)
        
        return changed
    
    def _is_excluded(self, file_path: Path, vault_path: Path, 
                     exclude_patterns: list[str]) -> bool:
        """Check if file matches any exclude pattern"""
        relative = file_path.relative_to(vault_path)
        for pattern in exclude_patterns:
            if relative.match(pattern):
                return True
        return False
    
    def log_sync_action(self, path: str, action: str, details: str = "") -> None:
        """Record action in history"""
        import time
        with self.conn:
            self.conn.execute(
                "INSERT INTO sync_history (obsidian_path, action, timestamp, details) VALUES (?, ?, ?, ?)",
                (path, action, int(time.time()), details)
            )
    
    def close(self) -> None:
        """Close database connection"""
        self.conn.close()
```

### 4.2 Write State Tests

Test cases:
- Create new database with schema
- Insert and retrieve sync record
- Update existing record
- Folder mapping CRUD operations
- Hash computation correctness
- Finding changed files logic
- Transaction rollback on error

### Acceptance Criteria
- [ ] Database created automatically
- [ ] Schema migrations work (future-proofing)
- [ ] CRUD operations all work correctly
- [ ] Content hash matches SHA-256 standard
- [ ] Changed file detection is accurate
- [ ] Exclusion patterns respected
- [ ] No SQL injection vulnerabilities
- [ ] Connection properly closed on exit
- [ ] WAL mode enabled for concurrent access

---

## Task 5: Markdown Parser

**Priority**: High  
**Estimated Time**: 8-12 hours  
**Dependencies**: Task 1

### 5.1 Implement parser.py with Mistune

**Consider sub-agent for complex formatting edge cases.**

```python
# src/rock_paper_sync/parser.py
import mistune
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import logging
import hashlib
from datetime import datetime

logger = logging.getLogger('rock_paper_sync.parser')

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

@dataclass
class TextFormat:
    start: int
    end: int
    style: FormatStyle
    metadata: dict[str, str] = field(default_factory=dict)

@dataclass
class ContentBlock:
    type: BlockType
    level: int
    text: str
    formatting: list[TextFormat] = field(default_factory=list)
    children: list['ContentBlock'] = field(default_factory=list)

@dataclass
class MarkdownDocument:
    path: Path
    title: str
    content: list[ContentBlock]
    frontmatter: dict[str, Any]
    last_modified: datetime
    content_hash: str

def parse_markdown_file(file_path: Path) -> MarkdownDocument:
    """Parse markdown file into structured document"""
    raw_content = file_path.read_text(encoding='utf-8')
    frontmatter, body = extract_frontmatter(raw_content)
    
    title = frontmatter.get('title', file_path.stem)
    blocks = parse_content(body)
    content_hash = hashlib.sha256(raw_content.encode('utf-8')).hexdigest()
    
    return MarkdownDocument(
        path=file_path,
        title=title,
        content=blocks,
        frontmatter=frontmatter,
        last_modified=datetime.fromtimestamp(file_path.stat().st_mtime),
        content_hash=content_hash
    )

def extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter from markdown content"""
    if not content.startswith('---'):
        return {}, content
    
    # Find closing ---
    end_index = content.find('---', 3)
    if end_index == -1:
        return {}, content
    
    yaml_content = content[3:end_index].strip()
    remaining = content[end_index + 3:].lstrip('\n')
    
    try:
        frontmatter = yaml.safe_load(yaml_content) or {}
    except yaml.YAMLError as e:
        logger.warning(f"Invalid YAML frontmatter: {e}")
        frontmatter = {}
    
    return frontmatter, remaining

def parse_content(markdown_text: str) -> list[ContentBlock]:
    """Convert markdown text to content blocks"""
    # Create mistune parser with AST renderer
    md = mistune.create_markdown(renderer='ast')
    ast = md(markdown_text)
    
    blocks = []
    for node in ast:
        block = ast_node_to_block(node)
        if block:
            blocks.append(block)
    
    return blocks

def ast_node_to_block(node: dict) -> ContentBlock | None:
    """Convert single AST node to ContentBlock"""
    node_type = node.get('type')
    
    if node_type == 'paragraph':
        text, formatting = extract_text_and_formatting(node.get('children', []))
        return ContentBlock(
            type=BlockType.PARAGRAPH,
            level=0,
            text=text,
            formatting=formatting
        )
    
    elif node_type == 'heading':
        text, formatting = extract_text_and_formatting(node.get('children', []))
        return ContentBlock(
            type=BlockType.HEADER,
            level=node.get('level', 1),
            text=text,
            formatting=formatting
        )
    
    elif node_type == 'list':
        # Handle list items
        items = []
        for item in node.get('children', []):
            if item.get('type') == 'list_item':
                item_text, item_fmt = extract_text_and_formatting(
                    item.get('children', [{}])[0].get('children', [])
                )
                items.append(ContentBlock(
                    type=BlockType.LIST_ITEM,
                    level=1,  # Track nesting level
                    text=item_text,
                    formatting=item_fmt
                ))
        return items[0] if len(items) == 1 else items  # May need adjustment
    
    elif node_type == 'block_code':
        return ContentBlock(
            type=BlockType.CODE_BLOCK,
            level=0,
            text=node.get('text', ''),
            formatting=[]
        )
    
    elif node_type == 'block_quote':
        text, formatting = extract_text_and_formatting(
            node.get('children', [{}])[0].get('children', [])
        )
        return ContentBlock(
            type=BlockType.BLOCKQUOTE,
            level=0,
            text=text,
            formatting=formatting
        )
    
    elif node_type == 'thematic_break':
        return ContentBlock(
            type=BlockType.HORIZONTAL_RULE,
            level=0,
            text='---',
            formatting=[]
        )
    
    return None

def extract_text_and_formatting(children: list[dict]) -> tuple[str, list[TextFormat]]:
    """
    Extract plain text and formatting ranges from inline children.
    
    Recursively processes inline elements (text, strong, emphasis, code, link)
    building up plain text string while tracking formatting positions.
    """
    text_parts = []
    formatting = []
    current_pos = 0
    
    for child in children:
        child_type = child.get('type')
        
        if child_type == 'text':
            text_parts.append(child.get('raw', ''))
            current_pos += len(child.get('raw', ''))
        
        elif child_type == 'strong':
            start_pos = current_pos
            inner_text, inner_fmt = extract_text_and_formatting(child.get('children', []))
            text_parts.append(inner_text)
            
            # Add bold formatting for this range
            formatting.append(TextFormat(
                start=start_pos,
                end=start_pos + len(inner_text),
                style=FormatStyle.BOLD
            ))
            # Include any nested formatting, adjusted for position
            for fmt in inner_fmt:
                formatting.append(TextFormat(
                    start=start_pos + fmt.start,
                    end=start_pos + fmt.end,
                    style=fmt.style,
                    metadata=fmt.metadata
                ))
            current_pos += len(inner_text)
        
        elif child_type == 'emphasis':
            start_pos = current_pos
            inner_text, inner_fmt = extract_text_and_formatting(child.get('children', []))
            text_parts.append(inner_text)
            
            formatting.append(TextFormat(
                start=start_pos,
                end=start_pos + len(inner_text),
                style=FormatStyle.ITALIC
            ))
            for fmt in inner_fmt:
                formatting.append(TextFormat(
                    start=start_pos + fmt.start,
                    end=start_pos + fmt.end,
                    style=fmt.style,
                    metadata=fmt.metadata
                ))
            current_pos += len(inner_text)
        
        elif child_type == 'codespan':
            text_parts.append(child.get('raw', ''))
            formatting.append(TextFormat(
                start=current_pos,
                end=current_pos + len(child.get('raw', '')),
                style=FormatStyle.CODE
            ))
            current_pos += len(child.get('raw', ''))
        
        elif child_type == 'link':
            start_pos = current_pos
            inner_text, inner_fmt = extract_text_and_formatting(child.get('children', []))
            url = child.get('link', '')
            
            # Add URL in parentheses after link text
            link_text = f"{inner_text} ({url})"
            text_parts.append(link_text)
            
            formatting.append(TextFormat(
                start=start_pos,
                end=start_pos + len(link_text),
                style=FormatStyle.LINK,
                metadata={'url': url}
            ))
            current_pos += len(link_text)
        
        elif child_type == 'image':
            # Replace image with placeholder
            alt_text = child.get('alt', 'Image')
            placeholder = f"[Image: {alt_text}]"
            text_parts.append(placeholder)
            current_pos += len(placeholder)
    
    return ''.join(text_parts), formatting
```

### 5.2 Create Test Fixtures

Create sample markdown files in `tests/fixtures/sample_markdown/`:

**simple.md**:
```markdown
This is a simple paragraph.

Another paragraph here.
```

**headers.md**:
```markdown
# Header 1
## Header 2
### Header 3
#### Header 4
##### Header 5
###### Header 6
```

**formatting.md**:
```markdown
This is **bold** text.

This is *italic* text.

This is ***bold and italic*** text.

This is `inline code`.

This is ~~strikethrough~~ text.
```

**lists.md**:
```markdown
- First item
- Second item
  - Nested item
  - Another nested
- Third item

1. Numbered one
2. Numbered two
   1. Nested numbered
3. Numbered three
```

**frontmatter.md**:
```markdown
---
title: Custom Title
author: John Doe
tags:
  - test
  - markdown
---

# Document Content

This document has frontmatter.
```

### 5.3 Write Parser Tests

Test each element type:
- Simple paragraphs
- Headers (all levels)
- Inline formatting (bold, italic, nested)
- Lists (bullet, numbered, nested)
- Code blocks (fenced, indented)
- Links (preserve URL)
- Images (placeholder)
- Frontmatter extraction
- Edge cases (empty file, only whitespace)

### Acceptance Criteria
- [ ] Parses all CommonMark elements
- [ ] Extracts frontmatter correctly
- [ ] Preserves inline formatting positions
- [ ] Handles nested formatting (bold within italic)
- [ ] Converts links to text + URL format
- [ ] Replaces images with placeholders
- [ ] Returns empty list for empty files
- [ ] Logs warning for malformed markdown
- [ ] 100% test coverage

---

## Task 6: reMarkable File Generator (CRITICAL)

**Priority**: Critical  
**Estimated Time**: 16-24 hours  
**Dependencies**: Task 5

### **🚨 THIS TASK REQUIRES A SUB-AGENT 🚨**

**Sub-agent context**: This is the most complex component. You need deep understanding of:
1. rmscene library internals
2. reMarkable v6 binary format
3. CRDT sequence structures
4. Scene tree organization

**Sub-agent should**:
1. Clone/install rmscene and explore source code
2. Read rmscene tests for usage examples
3. Examine real .rm files from device
4. Start with minimal working example
5. Incrementally add features
6. Document ALL findings and limitations

### 6.1 Study rmscene Library

```bash
pip install rmscene
python -c "import rmscene; help(rmscene)"
```

Key modules to examine:
- `rmscene.scene_items` - Data structures
- `rmscene.write_blocks` - Serialization
- `rmscene.read_blocks` - Parsing (for validation)
- `rmscene.crdt_sequence` - Text storage

### 6.2 Create Minimal Text Generation

Start with single page, single text item:

```python
# src/rock_paper_sync/generator.py
from rmscene import scene_items, write_blocks
import uuid
import io

def generate_minimal_rm_file() -> bytes:
    """Generate simplest possible .rm file with text"""
    # This is exploratory - actual implementation may differ
    # based on rmscene API
    
    # Create text content using CRDT sequence
    # ...
    
    # Create scene tree
    # ...
    
    # Serialize to bytes
    # ...
    pass

# Test by reading back
def validate_rm_file(rm_bytes: bytes) -> bool:
    """Verify generated file is parseable"""
    from rmscene import read_blocks
    blocks = list(read_blocks(io.BytesIO(rm_bytes)))
    return len(blocks) > 0
```

### 6.3 Implement Full Generator

```python
# src/rock_paper_sync/generator.py
from dataclasses import dataclass
from pathlib import Path
import uuid as uuid_module
import time
import json
import logging

from .parser import MarkdownDocument, ContentBlock, TextFormat, BlockType, FormatStyle
from .config import LayoutConfig

logger = logging.getLogger('rock_paper_sync.generator')

@dataclass
class TextItem:
    text: str
    x: float
    y: float
    width: float
    formatting: list[TextFormat]

@dataclass
class RemarkablePage:
    uuid: str
    text_items: list[TextItem]

@dataclass
class RemarkableDocument:
    uuid: str
    visible_name: str
    parent_uuid: str
    pages: list[RemarkablePage]
    created_time: int
    modified_time: int

class RemarkableGenerator:
    def __init__(self, layout_config: LayoutConfig):
        self.layout = layout_config
        self.page_height = 1872
        self.page_width = 1404
        self.line_height = 32  # pixels per line, approximate
        self.char_width = 10   # approximate character width
    
    def generate_document(self, md_doc: MarkdownDocument, 
                          parent_uuid: str = "") -> RemarkableDocument:
        """Convert MarkdownDocument to RemarkableDocument"""
        doc_uuid = str(uuid_module.uuid4())
        timestamp = int(time.time() * 1000)
        
        # Paginate content
        page_contents = self.paginate_content(md_doc.content)
        
        # Generate pages
        pages = []
        for page_blocks in page_contents:
            page_uuid = str(uuid_module.uuid4())
            text_items = self.blocks_to_text_items(page_blocks)
            pages.append(RemarkablePage(uuid=page_uuid, text_items=text_items))
        
        return RemarkableDocument(
            uuid=doc_uuid,
            visible_name=md_doc.title,
            parent_uuid=parent_uuid,
            pages=pages,
            created_time=timestamp,
            modified_time=timestamp
        )
    
    def paginate_content(self, blocks: list[ContentBlock]) -> list[list[ContentBlock]]:
        """Split content into pages based on line count"""
        pages = []
        current_page = []
        current_lines = 0
        
        for block in blocks:
            block_lines = self.estimate_block_lines(block)
            
            # Check if header should start new page
            if block.type == BlockType.HEADER:
                remaining_space = self.layout.lines_per_page - current_lines
                if remaining_space < 10 and current_page:
                    pages.append(current_page)
                    current_page = []
                    current_lines = 0
            
            # Check if block fits on current page
            if current_lines + block_lines > self.layout.lines_per_page:
                if current_page:
                    pages.append(current_page)
                current_page = [block]
                current_lines = block_lines
            else:
                current_page.append(block)
                current_lines += block_lines
        
        # Don't forget last page
        if current_page:
            pages.append(current_page)
        
        return pages if pages else [[]]  # At least one empty page
    
    def estimate_block_lines(self, block: ContentBlock) -> int:
        """Estimate how many lines a block will take"""
        if block.type == BlockType.HORIZONTAL_RULE:
            return 2
        
        # Calculate based on text length and available width
        available_width = self.page_width - self.layout.margin_left - self.layout.margin_right
        chars_per_line = int(available_width / self.char_width)
        
        text_lines = len(block.text) / chars_per_line
        
        # Add spacing
        if block.type == BlockType.HEADER:
            return int(text_lines) + 2  # Extra space after header
        elif block.type == BlockType.PARAGRAPH:
            return int(text_lines) + 1  # Space after paragraph
        else:
            return int(text_lines) + 1
    
    def blocks_to_text_items(self, blocks: list[ContentBlock]) -> list[TextItem]:
        """Convert blocks to positioned text items"""
        items = []
        y_position = float(self.layout.margin_top)
        
        for block in blocks:
            x_position = float(self.layout.margin_left)
            width = float(self.page_width - self.layout.margin_left - self.layout.margin_right)
            
            # Adjust for list indentation
            if block.type == BlockType.LIST_ITEM:
                indent = 20 * block.level
                x_position += indent
                width -= indent
                text = f"• {block.text}"
            else:
                text = block.text
            
            items.append(TextItem(
                text=text,
                x=x_position,
                y=y_position,
                width=width,
                formatting=block.formatting
            ))
            
            # Update Y position for next block
            lines = self.estimate_block_lines(block)
            y_position += lines * self.line_height
        
        return items
    
    def generate_rm_file(self, page: RemarkablePage) -> bytes:
        """
        Generate binary .rm file content using rmscene.
        
        ⚠️ THIS IS THE CRITICAL INTEGRATION POINT ⚠️
        
        Implementation depends on rmscene's actual API.
        Sub-agent needs to explore library to determine exact approach.
        """
        # TODO: Implement using rmscene
        # This is placeholder - actual implementation will differ
        
        # Example approach (conceptual):
        # 1. Create SceneTree
        # 2. Add RootTextBlock with CRDT sequence
        # 3. Apply formatting via optional properties
        # 4. Serialize to bytes
        
        raise NotImplementedError(
            "rmscene integration requires sub-agent exploration. "
            "See TASKS.md Task 6 for guidance."
        )
    
    def write_document_files(self, doc: RemarkableDocument, 
                             output_dir: Path) -> None:
        """Write all files for document to disk"""
        from .metadata import (
            generate_document_metadata,
            generate_content_metadata,
            generate_page_metadata
        )
        
        # Create document directory
        doc_dir = output_dir / doc.uuid
        doc_dir.mkdir(parents=True, exist_ok=True)
        
        # Write .metadata file
        metadata = generate_document_metadata(doc)
        (doc_dir / f"{doc.uuid}.metadata").write_text(
            json.dumps(metadata, indent=2)
        )
        
        # Write .content file
        content = generate_content_metadata(doc)
        (doc_dir / f"{doc.uuid}.content").write_text(
            json.dumps(content, indent=2)
        )
        
        # Write page files
        for page in doc.pages:
            # Write .rm file
            rm_bytes = self.generate_rm_file(page)
            (doc_dir / f"{page.uuid}.rm").write_bytes(rm_bytes)
            
            # Write page metadata
            page_meta = generate_page_metadata(page)
            (doc_dir / f"{page.uuid}-metadata.json").write_text(
                json.dumps(page_meta, indent=2)
            )
        
        logger.info(f"Wrote document {doc.uuid} with {len(doc.pages)} pages")
```

### 6.4 Implement Metadata Generator

```python
# src/rock_paper_sync/metadata.py
import time
import json

def generate_document_metadata(doc) -> dict:
    """Generate .metadata file content"""
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

def generate_content_metadata(doc) -> dict:
    """Generate .content file content"""
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

def generate_page_metadata(page) -> dict:
    """Generate page metadata JSON"""
    return {
        "layers": [
            {
                "name": "Layer 1",
                "visible": True
            }
        ]
    }

def generate_folder_metadata(name: str, parent_uuid: str) -> dict:
    """Generate folder (CollectionType) metadata"""
    timestamp = int(time.time() * 1000)
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

### 6.5 Write Generator Tests

Test cases:
- Single paragraph generates one page
- Long document splits into multiple pages
- Headers start new page when near bottom
- List items are properly indented
- Formatting is preserved in output
- Generated .rm file is valid (round-trip test)
- Metadata JSON is valid
- All required files are created

### Acceptance Criteria
- [ ] Generates valid v6 .rm files readable by rmscene
- [ ] Text content preserved exactly
- [ ] Formatting (bold/italic) encoded correctly
- [ ] Multi-page documents paginate properly
- [ ] Page breaks occur at sensible points
- [ ] All metadata files have correct structure
- [ ] Folder hierarchy generation works
- [ ] Files load on actual reMarkable device (manual test)
- [ ] Round-trip test passes (generate → parse → verify)

---

## Task 7: Sync Engine

**Priority**: High  
**Estimated Time**: 8-12 hours  
**Dependencies**: Tasks 4, 5, 6

### 7.1 Implement converter.py

```python
# src/rock_paper_sync/converter.py
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import uuid as uuid_module
import time
import logging

from .config import AppConfig
from .state import StateManager, SyncRecord
from .parser import parse_markdown_file
from .generator import RemarkableGenerator
from .metadata import generate_folder_metadata

logger = logging.getLogger('rock_paper_sync.converter')

@dataclass
class SyncResult:
    path: Path
    success: bool
    remarkable_uuid: Optional[str] = None
    page_count: Optional[int] = None
    error: Optional[str] = None

class SyncEngine:
    def __init__(self, config: AppConfig, state: StateManager):
        self.config = config
        self.state = state
        self.generator = RemarkableGenerator(config.layout)
    
    def sync_file(self, markdown_path: Path) -> SyncResult:
        """Full sync pipeline for single file"""
        try:
            # Parse markdown
            logger.info(f"Parsing {markdown_path}")
            md_doc = parse_markdown_file(markdown_path)
            
            # Check if needs sync
            relative_path = str(markdown_path.relative_to(
                self.config.sync.obsidian_vault
            ))
            current_state = self.state.get_file_state(relative_path)
            
            if current_state and current_state.content_hash == md_doc.content_hash:
                logger.debug(f"File unchanged, skipping: {relative_path}")
                return SyncResult(path=markdown_path, success=True,
                                 remarkable_uuid=current_state.remarkable_uuid,
                                 page_count=current_state.page_count)
            
            # Ensure parent folders exist
            parent_uuid = self.ensure_folder_hierarchy(markdown_path)
            
            # Generate reMarkable document
            logger.info(f"Generating RM document for {markdown_path}")
            rm_doc = self.generator.generate_document(md_doc, parent_uuid)
            
            # Write files to output
            self.generator.write_document_files(
                rm_doc,
                self.config.sync.remarkable_output
            )
            
            # Update state database
            new_state = SyncRecord(
                obsidian_path=relative_path,
                remarkable_uuid=rm_doc.uuid,
                content_hash=md_doc.content_hash,
                last_sync_time=int(time.time()),
                page_count=len(rm_doc.pages),
                status='synced'
            )
            self.state.update_file_state(new_state)
            self.state.log_sync_action(relative_path, 'synced',
                                       f"Generated {len(rm_doc.pages)} pages")
            
            logger.info(f"Successfully synced {markdown_path}")
            return SyncResult(
                path=markdown_path,
                success=True,
                remarkable_uuid=rm_doc.uuid,
                page_count=len(rm_doc.pages)
            )
        
        except Exception as e:
            logger.error(f"Failed to sync {markdown_path}: {e}")
            self.state.log_sync_action(str(markdown_path), 'error', str(e))
            return SyncResult(path=markdown_path, success=False, error=str(e))
    
    def sync_all_changed(self) -> list[SyncResult]:
        """Sync all files that have changed"""
        changed_files = self.state.find_changed_files(
            self.config.sync.obsidian_vault,
            self.config.sync.include_patterns,
            self.config.sync.exclude_patterns
        )
        
        logger.info(f"Found {len(changed_files)} files to sync")
        
        results = []
        for file_path in changed_files:
            result = self.sync_file(file_path)
            results.append(result)
        
        success_count = sum(1 for r in results if r.success)
        logger.info(f"Sync complete: {success_count}/{len(results)} succeeded")
        
        return results
    
    def ensure_folder_hierarchy(self, obsidian_path: Path) -> str:
        """Create RM folders for directory structure, return parent UUID"""
        relative_path = obsidian_path.relative_to(self.config.sync.obsidian_vault)
        
        if not relative_path.parent.parts:
            # File is in vault root
            return ""
        
        parent_uuid = ""
        folder_path_parts = []
        
        for part in relative_path.parent.parts:
            folder_path_parts.append(part)
            folder_path = "/".join(folder_path_parts)
            
            existing_uuid = self.state.get_folder_uuid(folder_path)
            
            if existing_uuid:
                parent_uuid = existing_uuid
            else:
                # Create new folder
                new_uuid = str(uuid_module.uuid4())
                self._create_rm_folder(part, new_uuid, parent_uuid)
                self.state.create_folder_mapping(folder_path, new_uuid)
                parent_uuid = new_uuid
                logger.info(f"Created folder: {folder_path} -> {new_uuid}")
        
        return parent_uuid
    
    def _create_rm_folder(self, name: str, uuid: str, parent_uuid: str) -> None:
        """Create reMarkable folder metadata files"""
        import json
        
        folder_dir = self.config.sync.remarkable_output / uuid
        folder_dir.mkdir(parents=True, exist_ok=True)
        
        metadata = generate_folder_metadata(name, parent_uuid)
        (folder_dir / f"{uuid}.metadata").write_text(json.dumps(metadata, indent=2))
```

### 7.2 Write Sync Engine Tests

Test cases:
- Single file sync end-to-end
- Multiple files sync correctly
- Unchanged files are skipped
- Folder hierarchy created correctly
- Nested folders preserve parent relationships
- Error in one file doesn't stop others
- State database updated correctly
- Sync history logged

### Acceptance Criteria
- [ ] Full pipeline works end-to-end
- [ ] Incremental sync skips unchanged files
- [ ] Folder structure mirrors Obsidian
- [ ] Errors logged but don't crash
- [ ] State database consistent after sync
- [ ] Dry-run mode supported
- [ ] Progress reported during sync

---

## Task 8: File Watcher

**Priority**: Medium  
**Estimated Time**: 4-6 hours  
**Dependencies**: Task 7

### 8.1 Implement watcher.py

```python
# src/rock_paper_sync/watcher.py
from pathlib import Path
from typing import Callable
import threading
import time
import logging

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = logging.getLogger('rock_paper_sync.watcher')

class ChangeHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[Path], None], 
                 debounce_seconds: int = 5):
        super().__init__()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.pending: dict[str, float] = {}
        self.lock = threading.Lock()
    
    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith('.md'):
            logger.debug(f"File modified: {event.src_path}")
            self._queue_change(event.src_path)
    
    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith('.md'):
            logger.debug(f"File created: {event.src_path}")
            self._queue_change(event.src_path)
    
    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith('.md'):
            logger.debug(f"File deleted: {event.src_path}")
            # Mark for deletion handling (Phase 2 feature)
            pass
    
    def _queue_change(self, path: str) -> None:
        with self.lock:
            self.pending[path] = time.time()
    
    def process_pending(self) -> list[Path]:
        """Check for changes past debounce window"""
        ready = []
        now = time.time()
        
        with self.lock:
            expired = []
            for path, timestamp in self.pending.items():
                if now - timestamp >= self.debounce_seconds:
                    ready.append(Path(path))
                    expired.append(path)
            
            for path in expired:
                del self.pending[path]
        
        return ready

class VaultWatcher:
    def __init__(self, vault_path: Path, 
                 on_change: Callable[[Path], None],
                 debounce_seconds: int = 5):
        self.vault_path = vault_path
        self.on_change = on_change
        self.handler = ChangeHandler(on_change, debounce_seconds)
        self.observer = Observer()
        self.running = False
        self._process_thread: threading.Thread | None = None
    
    def start(self) -> None:
        """Start watching vault directory"""
        logger.info(f"Starting file watcher on {self.vault_path}")
        
        self.observer.schedule(self.handler, str(self.vault_path), recursive=True)
        self.observer.start()
        self.running = True
        
        # Start pending processor thread
        self._process_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._process_thread.start()
    
    def _process_loop(self) -> None:
        """Continuously check for pending changes"""
        while self.running:
            ready = self.handler.process_pending()
            for path in ready:
                try:
                    logger.info(f"Processing change: {path}")
                    self.on_change(path)
                except Exception as e:
                    logger.error(f"Error processing {path}: {e}")
            time.sleep(1)
    
    def stop(self) -> None:
        """Stop watching"""
        logger.info("Stopping file watcher")
        self.running = False
        self.observer.stop()
        self.observer.join()
        if self._process_thread:
            self._process_thread.join(timeout=5)
```

### 8.2 Write Watcher Tests

Test cases:
- Detects file creation
- Detects file modification
- Debounce prevents duplicate processing
- Multiple rapid changes coalesce
- Recursive watching works
- Graceful shutdown
- Thread safety

### Acceptance Criteria
- [ ] Monitors vault recursively
- [ ] Debounces rapid changes
- [ ] Triggers callback after debounce period
- [ ] Thread-safe operation
- [ ] Clean shutdown on SIGINT
- [ ] Low CPU usage when idle

---

## Task 9: CLI Interface

**Priority**: Medium  
**Estimated Time**: 4-6 hours  
**Dependencies**: Tasks 2, 7, 8

### 9.1 Implement cli.py

```python
# src/rock_paper_sync/cli.py
import click
from pathlib import Path
import signal
import sys
import logging

from .config import load_config, validate_config
from .logging_setup import setup_logging
from .state import StateManager
from .converter import SyncEngine
from .watcher import VaultWatcher

@click.group()
@click.option('--config', '-c', 
              default='~/.config/rock-paper-sync/config.toml',
              help='Path to config file')
@click.option('--verbose', '-v', is_flag=True, help='Enable debug logging')
@click.pass_context
def main(ctx: click.Context, config: str, verbose: bool) -> None:
    """reMarkable-Obsidian Sync Tool"""
    ctx.ensure_object(dict)
    
    config_path = Path(config).expanduser()
    if not config_path.exists():
        click.echo(f"Error: Config file not found: {config_path}", err=True)
        click.echo(f"Create one using: rock-paper-sync init", err=True)
        sys.exit(1)
    
    try:
        app_config = load_config(config_path)
        validate_config(app_config)
    except Exception as e:
        click.echo(f"Error loading config: {e}", err=True)
        sys.exit(1)
    
    # Override log level if verbose
    log_level = 'debug' if verbose else app_config.log_level
    setup_logging(log_level, app_config.log_file)
    
    ctx.obj['config'] = app_config
    ctx.obj['logger'] = logging.getLogger('rock_paper_sync')

@main.command()
@click.option('--dry-run', is_flag=True, help='Preview without writing')
@click.pass_context
def sync(ctx: click.Context, dry_run: bool) -> None:
    """Sync all changed files once"""
    config = ctx.obj['config']
    logger = ctx.obj['logger']
    
    state = StateManager(config.sync.state_database)
    engine = SyncEngine(config, state)
    
    if dry_run:
        click.echo("Dry run mode - no files will be written")
        # TODO: Implement dry run logic
        return
    
    click.echo(f"Scanning {config.sync.obsidian_vault}...")
    results = engine.sync_all_changed()
    
    success_count = sum(1 for r in results if r.success)
    click.echo(f"Synced {success_count}/{len(results)} files")
    
    for result in results:
        if result.success:
            click.echo(f"  ✓ {result.path.name} ({result.page_count} pages)")
        else:
            click.echo(f"  ✗ {result.path.name}: {result.error}", err=True)
    
    state.close()

@main.command()
@click.pass_context
def watch(ctx: click.Context) -> None:
    """Continuously monitor for changes"""
    config = ctx.obj['config']
    logger = ctx.obj['logger']
    
    state = StateManager(config.sync.state_database)
    engine = SyncEngine(config, state)
    
    def on_file_change(path: Path) -> None:
        result = engine.sync_file(path)
        if result.success:
            click.echo(f"Synced: {path.name} ({result.page_count} pages)")
        else:
            click.echo(f"Error syncing {path.name}: {result.error}", err=True)
    
    watcher = VaultWatcher(
        config.sync.obsidian_vault,
        on_file_change,
        config.sync.debounce_seconds
    )
    
    # Handle graceful shutdown
    def shutdown(signum, frame):
        click.echo("\nShutting down...")
        watcher.stop()
        state.close()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    
    click.echo(f"Watching {config.sync.obsidian_vault}")
    click.echo("Press Ctrl+C to stop")
    
    watcher.start()
    
    # Keep main thread alive
    try:
        while True:
            signal.pause()
    except AttributeError:
        # Windows doesn't have signal.pause()
        import time
        while True:
            time.sleep(1)

@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show sync status"""
    config = ctx.obj['config']
    
    state = StateManager(config.sync.state_database)
    
    # Get sync statistics
    cursor = state.conn.execute(
        "SELECT status, COUNT(*) FROM sync_state GROUP BY status"
    )
    stats = dict(cursor.fetchall())
    
    click.echo("Sync Status:")
    click.echo(f"  Synced: {stats.get('synced', 0)}")
    click.echo(f"  Pending: {stats.get('pending', 0)}")
    click.echo(f"  Errors: {stats.get('error', 0)}")
    
    # Recent activity
    cursor = state.conn.execute(
        "SELECT obsidian_path, action, timestamp FROM sync_history "
        "ORDER BY timestamp DESC LIMIT 10"
    )
    
    click.echo("\nRecent Activity:")
    for row in cursor.fetchall():
        from datetime import datetime
        dt = datetime.fromtimestamp(row[2])
        click.echo(f"  {dt.strftime('%Y-%m-%d %H:%M')} {row[1]}: {row[0]}")
    
    state.close()

@main.command()
@click.pass_context
def reset(ctx: click.Context) -> None:
    """Clear sync state (force full re-sync)"""
    config = ctx.obj['config']
    
    if not click.confirm("This will clear all sync state. Continue?"):
        return
    
    state_db = config.sync.state_database
    if state_db.exists():
        state_db.unlink()
        click.echo("Sync state cleared")
    else:
        click.echo("No sync state to clear")

@main.command()
@click.argument('output', type=click.Path())
def init(output: str) -> None:
    """Create example config file"""
    output_path = Path(output).expanduser()
    
    if output_path.exists():
        if not click.confirm(f"{output_path} exists. Overwrite?"):
            return
    
    example_config = '''# reMarkable-Obsidian Sync Configuration
# Edit paths below to match your setup

[paths]
obsidian_vault = "~/obsidian-vault"
remarkable_output = "~/remarkable-sync"
state_database = "~/.local/share/rock-paper-sync/state.db"

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
file = "~/.local/share/rock-paper-sync/sync.log"
'''
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(example_config)
    click.echo(f"Created config file: {output_path}")
    click.echo("Edit the file to set your paths")

if __name__ == '__main__':
    main()
```

### 9.2 Write CLI Tests

Test cases:
- `--help` shows usage
- Invalid config path exits with error
- Sync command processes files
- Watch command starts watcher
- Status shows statistics
- Reset clears database
- Init creates config file

### Acceptance Criteria
- [ ] All commands work as specified
- [ ] Helpful error messages
- [ ] Graceful shutdown on Ctrl+C
- [ ] Progress indication during operations
- [ ] Verbose mode shows debug info
- [ ] Dry-run prevents actual writes

---

## Task 10: Integration Testing

**Priority**: High  
**Estimated Time**: 8-12 hours  
**Dependencies**: All previous tasks

### 10.1 Create Integration Test Suite

```python
# tests/test_integration.py
import pytest
from pathlib import Path
import tempfile
import time

from rock_paper_sync.config import AppConfig, SyncConfig, LayoutConfig
from rock_paper_sync.state import StateManager
from rock_paper_sync.converter import SyncEngine

@pytest.fixture
def integration_setup(tmp_path):
    """Set up complete integration test environment"""
    vault = tmp_path / "vault"
    vault.mkdir()
    
    output = tmp_path / "output"
    output.mkdir()
    
    db = tmp_path / "state.db"
    
    config = AppConfig(
        sync=SyncConfig(
            obsidian_vault=vault,
            remarkable_output=output,
            state_database=db,
            include_patterns=["**/*.md"],
            exclude_patterns=[],
            debounce_seconds=1
        ),
        layout=LayoutConfig(
            lines_per_page=45,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        ),
        log_level="debug",
        log_file=tmp_path / "test.log"
    )
    
    state = StateManager(db)
    engine = SyncEngine(config, state)
    
    yield {
        'vault': vault,
        'output': output,
        'config': config,
        'state': state,
        'engine': engine
    }
    
    state.close()

def test_full_sync_pipeline(integration_setup):
    """Test complete markdown → reMarkable pipeline"""
    vault = integration_setup['vault']
    output = integration_setup['output']
    engine = integration_setup['engine']
    
    # Create test markdown file
    test_md = vault / "test.md"
    test_md.write_text("""---
title: Test Document
---

# Introduction

This is a **test** document with *formatting*.

## Section 2

- Item 1
- Item 2
- Item 3
""")
    
    # Sync
    results = engine.sync_all_changed()
    
    # Verify results
    assert len(results) == 1
    assert results[0].success
    assert results[0].page_count >= 1
    
    # Verify output files exist
    uuid = results[0].remarkable_uuid
    doc_dir = output / uuid
    assert doc_dir.exists()
    assert (doc_dir / f"{uuid}.metadata").exists()
    assert (doc_dir / f"{uuid}.content").exists()
    
    # Verify at least one page was created
    rm_files = list(doc_dir.glob("*.rm"))
    assert len(rm_files) >= 1

def test_incremental_sync(integration_setup):
    """Unchanged files should not be reprocessed"""
    vault = integration_setup['vault']
    engine = integration_setup['engine']
    
    test_md = vault / "test.md"
    test_md.write_text("# Test\n\nContent here.")
    
    # First sync
    results1 = engine.sync_all_changed()
    assert len(results1) == 1
    
    # Second sync (no changes)
    results2 = engine.sync_all_changed()
    assert len(results2) == 0  # Nothing changed

def test_folder_hierarchy(integration_setup):
    """Folder structure should be preserved"""
    vault = integration_setup['vault']
    output = integration_setup['output']
    engine = integration_setup['engine']
    state = integration_setup['state']
    
    # Create nested structure
    nested = vault / "projects" / "work"
    nested.mkdir(parents=True)
    (nested / "notes.md").write_text("# Work Notes\n\nContent.")
    
    # Sync
    results = engine.sync_all_changed()
    assert results[0].success
    
    # Verify folders created
    projects_uuid = state.get_folder_uuid("projects")
    work_uuid = state.get_folder_uuid("projects/work")
    
    assert projects_uuid is not None
    assert work_uuid is not None
    
    # Verify folder metadata
    projects_meta = (output / projects_uuid / f"{projects_uuid}.metadata")
    assert projects_meta.exists()

def test_error_recovery(integration_setup):
    """Errors should be logged but not stop processing"""
    vault = integration_setup['vault']
    engine = integration_setup['engine']
    
    # Create valid file
    (vault / "good.md").write_text("# Good\n\nContent.")
    
    # Create problematic file (implementation-specific)
    # This test depends on what actually causes errors
    
    results = engine.sync_all_changed()
    # Should process what it can, report errors for rest
    assert len(results) >= 1
```

### 10.2 Manual Device Testing Checklist

Create test document, transfer to device via Syncthing, verify:

- [ ] Document appears in correct folder
- [ ] Document name matches title
- [ ] Text is readable
- [ ] Bold formatting renders correctly
- [ ] Italic formatting renders correctly
- [ ] Multi-page documents paginate
- [ ] List items are indented
- [ ] Can write annotations on document
- [ ] Document editable on device
- [ ] No error messages on device

### Acceptance Criteria
- [ ] Full pipeline test passes
- [ ] Incremental sync works correctly
- [ ] Folder hierarchy preserved
- [ ] Error recovery doesn't crash
- [ ] All edge cases handled
- [ ] Manual device testing passes

---

## Task 11: Documentation and Polish

**Priority**: Medium  
**Estimated Time**: 4-6 hours  
**Dependencies**: All previous tasks

### 11.1 Update README.md

Include:
- Project description
- Installation instructions
- Quick start guide
- Configuration reference
- CLI command reference
- Known limitations
- Contributing guide

### 11.2 Add Type Hints

Ensure all public functions have complete type hints:

```bash
mypy src/rock_paper_sync --strict
```

### 11.3 Format Code

```bash
black src/ tests/
ruff check src/ tests/ --fix
```

### 11.4 Generate Test Coverage Report

```bash
pytest --cov=rock_paper_sync --cov-report=html tests/
```

Target: >80% coverage

### 11.5 Create Example Usage

Document common workflows:
- First-time setup
- Daily usage
- Troubleshooting
- Advanced configuration

### Acceptance Criteria
- [ ] README is comprehensive
- [ ] All functions have type hints
- [ ] mypy passes strict mode
- [ ] Code formatted with black
- [ ] No ruff warnings
- [ ] Test coverage >80%
- [ ] Examples are clear and tested

---

## Sub-Agent Summary

Throughout implementation, spawn sub-agents for:

1. **rmscene Integration** (Task 6)
   - Deep dive into library internals
   - Generate working text files
   - Document API quirks

2. **Markdown Edge Cases** (Task 5)
   - Nested formatting
   - Complex lists
   - Unusual markdown syntax

3. **State Database Optimization** (Task 4)
   - Query performance
   - Migration strategies
   - Concurrent access

4. **Test Coverage Completion** (Task 10)
   - Edge case identification
   - Fixture creation
   - Mock strategies

Each sub-agent should focus deeply on their area while main agent orchestrates overall progress.

---

## Definition of Done

Phase 1 is complete when:

1. ✅ All 11 tasks pass acceptance criteria
2. ✅ Integration tests pass
3. ✅ Manual device testing passes
4. ✅ Documentation complete
5. ✅ Code quality checks pass
6. ✅ Test coverage >80%
7. ✅ No known critical bugs
8. ✅ Performance targets met

**Estimated Total Time**: 4-6 weeks of focused development
