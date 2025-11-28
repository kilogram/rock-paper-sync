"""Content hashing utilities for semantic comparison.

This module provides hash functions that enable accurate change detection while
ignoring non-semantic differences (whitespace, frontmatter, markers).

Hash Types
----------

1. **Semantic Hash** (`compute_semantic_hash`):
   - Based on AST structure (ContentBlock list)
   - Stable across whitespace changes, frontmatter edits, marker additions
   - Changes when actual content or formatting changes
   - Used for sync change detection

2. **File Hash** (`compute_file_hash`):
   - Based on raw file bytes
   - Changes on any file modification
   - Used for detecting external edits

3. **Paragraph Hash** (`compute_paragraph_hash`):
   - Based on normalized paragraph text
   - Used for paragraph-level change detection

Key Design Decision
-------------------

The semantic hash uses the **parsed AST** (ContentBlock list) rather than raw text.
This ensures the hash represents "what renders" not "how it's written."

Examples:

    # Same semantic hash (different source formatting)
    >>> doc1 = parse_markdown("# Header\\n\\nParagraph")
    >>> doc2 = parse_markdown("# Header\\n\\n\\n\\nParagraph")  # Extra newlines
    >>> compute_semantic_hash(doc1.content) == compute_semantic_hash(doc2.content)
    True

    # Different semantic hash (different content)
    >>> doc3 = parse_markdown("# Header\\n\\nDifferent")
    >>> compute_semantic_hash(doc1.content) == compute_semantic_hash(doc3.content)
    False
"""

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .parser import ContentBlock


def compute_semantic_hash(content_blocks: list["ContentBlock"]) -> str:
    """Compute hash of semantic content (what renders).

    This hash is **stable** across:
    - Extra whitespace/newlines in source
    - Frontmatter changes (already stripped by parser)
    - HTML comments (annotation markers)
    - Source formatting variations (e.g., `# Header` vs `Header\\n===`)

    This hash **changes** when:
    - Text content changes
    - Block structure changes (headings, lists, paragraphs)
    - Inline formatting changes (bold, italic, code)
    - Block ordering changes

    Implementation:
        Converts each ContentBlock to a canonical JSON representation,
        then hashes the combined JSON. Uses deterministic JSON serialization
        (sort_keys=True) to ensure consistent hashing.

    Args:
        content_blocks: Parsed ContentBlock list from parser

    Returns:
        SHA-256 hex digest of canonical representation (64 hex characters)

    Example:
        >>> from parser import parse_markdown_file
        >>> doc = parse_markdown_file(Path("document.md"))
        >>> semantic_hash = compute_semantic_hash(doc.content)
        >>> print(len(semantic_hash))  # Always 64 characters
        64
    """
    canonical_parts = []

    for block in content_blocks:
        # Convert block to canonical dictionary
        block_dict = _block_to_dict(block)

        # Serialize to JSON with sorted keys (deterministic)
        canonical_json = json.dumps(block_dict, sort_keys=True)
        canonical_parts.append(canonical_json)

    # Combine all blocks with newline separator
    combined = "\n".join(canonical_parts)

    # Hash the combined canonical representation
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _block_to_dict(block: "ContentBlock") -> dict:
    """Convert ContentBlock to canonical dictionary for hashing.

    Args:
        block: ContentBlock to convert

    Returns:
        Dictionary with canonical representation
    """
    result = {
        "type": block.type.value,
        "level": block.level,
        "text": _normalize_text(block.text),
    }

    # Include inline formatting if present
    if block.formatting:
        result["formatting"] = [
            {
                "style": f.style.value,
                "start": f.start,
                "end": f.end,
            }
            # Sort by position for deterministic ordering
            for f in sorted(block.formatting, key=lambda x: (x.start, x.end))
        ]

    # Include nested children if present (lists, blockquotes)
    if block.children:
        result["children"] = [_block_to_dict(child) for child in block.children]

    return result


def _normalize_text(text: str) -> str:
    """Normalize text content for hashing.

    Collapses whitespace while preserving semantic content.
    Does NOT change case (case is semantic).

    Args:
        text: Raw text content

    Returns:
        Normalized text with collapsed whitespace

    Example:
        >>> _normalize_text("  hello   world  ")
        'hello world'
    """
    # Strip leading/trailing whitespace
    normalized = text.strip()

    # Collapse multiple spaces/newlines to single space
    normalized = " ".join(normalized.split())

    return normalized


def compute_file_hash(file_path: Path) -> str:
    """Compute hash of raw file bytes.

    This hash changes when **anything** in the file changes:
    - Content changes
    - Whitespace changes
    - Comment/marker additions
    - Frontmatter changes

    Used for detecting external file modifications (e.g., user edited in text editor).

    Args:
        file_path: Path to file to hash

    Returns:
        SHA-256 hex digest of raw file bytes

    Example:
        >>> file_hash = compute_file_hash(Path("document.md"))
        >>> # Edit file externally
        >>> new_hash = compute_file_hash(Path("document.md"))
        >>> assert file_hash != new_hash  # Detects any change
    """
    hasher = hashlib.sha256()

    with open(file_path, "rb") as f:
        # Read in chunks for memory efficiency with large files
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)

    return hasher.hexdigest()


def compute_paragraph_hash(text: str) -> str:
    """Compute hash of paragraph text for change detection.

    Normalizes text before hashing to ignore trivial whitespace differences.
    Useful for comparing paragraph versions and detecting modifications.

    Args:
        text: Paragraph text content

    Returns:
        SHA-256 hex digest of normalized text

    Example:
        >>> hash1 = compute_paragraph_hash("Hello world")
        >>> hash2 = compute_paragraph_hash("Hello  world  ")  # Extra spaces
        >>> assert hash1 == hash2  # Whitespace ignored
    """
    normalized = _normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_content_hash_from_text(text: str) -> str:
    """Compute hash from raw text content (legacy compatibility).

    This is a simpler hash function for backward compatibility.
    Prefer `compute_semantic_hash` for new code.

    Args:
        text: Text content to hash

    Returns:
        SHA-256 hex digest of normalized text
    """
    normalized = _normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
