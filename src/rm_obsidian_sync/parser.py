"""Markdown parser for reMarkable-Obsidian sync.

This module parses markdown files into structured content blocks that can be
converted to reMarkable format. It uses mistune 3.0+ for AST-based parsing and
preserves inline formatting with exact character positions.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import mistune
import yaml

logger = logging.getLogger("rm_obsidian_sync.parser")


class BlockType(Enum):
    """Types of content blocks in a markdown document."""

    PARAGRAPH = "paragraph"
    HEADER = "header"
    LIST_ITEM = "list_item"
    CODE_BLOCK = "code"
    BLOCKQUOTE = "blockquote"
    HORIZONTAL_RULE = "hr"


class FormatStyle(Enum):
    """Styles of inline text formatting."""

    BOLD = "bold"
    ITALIC = "italic"
    CODE = "code"
    LINK = "link"
    STRIKETHROUGH = "strikethrough"


@dataclass
class TextFormat:
    """Inline formatting range within text.

    Attributes:
        start: Character offset where formatting begins (inclusive)
        end: Character offset where formatting ends (exclusive)
        style: Type of formatting applied
        metadata: Additional information (e.g., URL for links)
    """

    start: int
    end: int
    style: FormatStyle
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class ContentBlock:
    """A block-level element in a markdown document.

    Attributes:
        type: Type of block (paragraph, header, list item, etc.)
        level: Semantic level (1-6 for headers, depth for lists, 0 otherwise)
        text: Plain text content (formatting stripped)
        formatting: List of inline formatting ranges
        children: Nested content blocks (for lists or quotes)
    """

    type: BlockType
    level: int
    text: str
    formatting: list[TextFormat] = field(default_factory=list)
    children: list["ContentBlock"] = field(default_factory=list)


@dataclass
class MarkdownDocument:
    """Complete parsed markdown document.

    Attributes:
        path: Original file path
        title: Document title (from frontmatter or filename)
        content: List of content blocks
        frontmatter: YAML frontmatter data
        last_modified: File modification timestamp
        content_hash: SHA-256 hash of raw content
    """

    path: Path
    title: str
    content: list[ContentBlock]
    frontmatter: dict[str, Any]
    last_modified: datetime
    content_hash: str


def parse_markdown_file(file_path: Path) -> MarkdownDocument:
    """Parse a markdown file into a structured document.

    Args:
        file_path: Path to markdown file

    Returns:
        Parsed markdown document

    Raises:
        FileNotFoundError: If file doesn't exist
        UnicodeDecodeError: If file is not valid UTF-8
    """
    logger.debug(f"Parsing markdown file: {file_path}")

    # Read file content
    raw_content = file_path.read_text(encoding="utf-8")

    # Extract frontmatter and body
    frontmatter, body = extract_frontmatter(raw_content)

    # Parse content blocks
    blocks = parse_content(body)

    # Determine title
    title = frontmatter.get("title")
    if not title:
        # Use filename without extension as fallback
        title = file_path.stem

    # Compute content hash
    content_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

    # Get file modification time
    last_modified = datetime.fromtimestamp(file_path.stat().st_mtime)

    logger.debug(
        f"Parsed {len(blocks)} blocks from {file_path.name}, "
        f"hash: {content_hash[:8]}..."
    )

    return MarkdownDocument(
        path=file_path,
        title=title,
        content=blocks,
        frontmatter=frontmatter,
        last_modified=last_modified,
        content_hash=content_hash,
    )


def extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter from markdown content.

    Frontmatter must be at the very beginning of the file, enclosed by --- markers.

    Args:
        content: Raw markdown content

    Returns:
        Tuple of (frontmatter_dict, remaining_content)
    """
    # Check if content starts with frontmatter delimiter
    if not content.startswith("---"):
        return {}, content

    # Find closing delimiter
    # Must be on its own line, so look for \n---\n or \n--- at end of file
    lines = content.split("\n")
    if len(lines) < 3:  # Need at least ---, content, ---
        return {}, content

    # Find the closing --- (skip the opening one)
    closing_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_index = i
            break

    if closing_index is None:
        # No closing delimiter found, treat as regular content
        logger.warning("Unclosed frontmatter delimiter, treating as regular content")
        return {}, content

    # Extract YAML content (between the delimiters)
    yaml_content = "\n".join(lines[1:closing_index])
    remaining_lines = lines[closing_index + 1 :]
    remaining_content = "\n".join(remaining_lines).lstrip("\n")

    # Parse YAML
    try:
        frontmatter = yaml.safe_load(yaml_content) or {}
        if not isinstance(frontmatter, dict):
            logger.warning(f"Frontmatter is not a dict: {type(frontmatter)}")
            frontmatter = {}
    except yaml.YAMLError as e:
        logger.warning(f"Invalid YAML frontmatter: {e}")
        frontmatter = {}

    return frontmatter, remaining_content


def parse_content(markdown_text: str) -> list[ContentBlock]:
    """Convert markdown text to content blocks.

    Args:
        markdown_text: Markdown text (without frontmatter)

    Returns:
        List of content blocks
    """
    if not markdown_text.strip():
        return []

    # Create mistune parser with AST renderer
    md = mistune.create_markdown(renderer="ast")
    try:
        ast = md(markdown_text)
    except Exception as e:
        logger.error(f"Failed to parse markdown: {e}")
        return []

    # Convert AST nodes to content blocks
    blocks: list[ContentBlock] = []
    for node in ast:
        result = ast_node_to_block(node)
        if result is not None:
            # Handle both single blocks and lists of blocks
            if isinstance(result, list):
                blocks.extend(result)
            else:
                blocks.append(result)

    return blocks


def ast_node_to_block(
    node: dict[str, Any], list_level: int = 1
) -> ContentBlock | list[ContentBlock] | None:
    """Convert a single AST node to a ContentBlock.

    Args:
        node: Mistune AST node
        list_level: Current list nesting level (for recursive list processing)

    Returns:
        ContentBlock, list of ContentBlocks, or None if node type is not handled
    """
    node_type = node.get("type")

    if node_type == "paragraph":
        text, formatting = extract_text_and_formatting(node.get("children", []))
        return ContentBlock(
            type=BlockType.PARAGRAPH, level=0, text=text, formatting=formatting
        )

    elif node_type == "heading":
        text, formatting = extract_text_and_formatting(node.get("children", []))
        level = node.get("attrs", {}).get("level", 1)
        return ContentBlock(
            type=BlockType.HEADER, level=level, text=text, formatting=formatting
        )

    elif node_type == "list":
        # Process list items
        items: list[ContentBlock] = []
        ordered = node.get("attrs", {}).get("ordered", False)

        for item_node in node.get("children", []):
            if item_node.get("type") == "list_item":
                # List items contain child blocks (usually block_text or nested lists)
                item_children = item_node.get("children", [])

                for child in item_children:
                    child_type = child.get("type")
                    if child_type in ("paragraph", "block_text"):
                        # Extract text from paragraph or block_text
                        text, formatting = extract_text_and_formatting(
                            child.get("children", [])
                        )
                        items.append(
                            ContentBlock(
                                type=BlockType.LIST_ITEM,
                                level=list_level,
                                text=text,
                                formatting=formatting,
                            )
                        )
                    elif child_type == "list":
                        # Nested list - recursively process with increased level
                        nested_result = ast_node_to_block(child, list_level + 1)
                        if isinstance(nested_result, list):
                            items.extend(nested_result)
                        elif nested_result is not None:
                            items.append(nested_result)

        return items

    elif node_type == "block_code":
        # Code blocks - get raw text
        code_text = node.get("raw", "")
        lang = node.get("attrs", {}).get("info", "")
        # Include language info in the text if present
        if lang:
            code_text = f"[{lang}]\n{code_text}"
        return ContentBlock(
            type=BlockType.CODE_BLOCK, level=0, text=code_text, formatting=[]
        )

    elif node_type == "block_quote":
        # Blockquotes contain child blocks
        quote_children = node.get("children", [])
        if quote_children:
            # Take the first child (usually a paragraph)
            first_child = quote_children[0]
            if first_child.get("type") == "paragraph":
                text, formatting = extract_text_and_formatting(
                    first_child.get("children", [])
                )
                return ContentBlock(
                    type=BlockType.BLOCKQUOTE, level=0, text=text, formatting=formatting
                )

    elif node_type == "thematic_break":
        return ContentBlock(
            type=BlockType.HORIZONTAL_RULE, level=0, text="---", formatting=[]
        )

    # Unknown or unhandled node type
    logger.debug(f"Unhandled AST node type: {node_type}")
    return None


def extract_text_and_formatting(
    children: list[dict[str, Any]]
) -> tuple[str, list[TextFormat]]:
    """Extract plain text and formatting ranges from inline AST nodes.

    This is the most critical function for preserving exact character positions
    of inline formatting like bold, italic, code, and links.

    Args:
        children: List of inline AST nodes from mistune

    Returns:
        Tuple of (plain_text, formatting_list)
    """
    text_parts: list[str] = []
    formatting: list[TextFormat] = []
    current_pos = 0

    for child in children:
        child_type = child.get("type")

        if child_type == "text":
            # Plain text - just append
            raw_text = child.get("raw", "")
            text_parts.append(raw_text)
            current_pos += len(raw_text)

        elif child_type == "strong":
            # Bold text
            start_pos = current_pos
            inner_text, inner_fmt = extract_text_and_formatting(
                child.get("children", [])
            )
            text_parts.append(inner_text)

            # Add bold formatting for this range
            formatting.append(
                TextFormat(
                    start=start_pos, end=start_pos + len(inner_text), style=FormatStyle.BOLD
                )
            )

            # Include any nested formatting, adjusted for position
            for fmt in inner_fmt:
                formatting.append(
                    TextFormat(
                        start=start_pos + fmt.start,
                        end=start_pos + fmt.end,
                        style=fmt.style,
                        metadata=fmt.metadata,
                    )
                )

            current_pos += len(inner_text)

        elif child_type == "emphasis":
            # Italic text
            start_pos = current_pos
            inner_text, inner_fmt = extract_text_and_formatting(
                child.get("children", [])
            )
            text_parts.append(inner_text)

            # Add italic formatting for this range
            formatting.append(
                TextFormat(
                    start=start_pos,
                    end=start_pos + len(inner_text),
                    style=FormatStyle.ITALIC,
                )
            )

            # Include any nested formatting, adjusted for position
            for fmt in inner_fmt:
                formatting.append(
                    TextFormat(
                        start=start_pos + fmt.start,
                        end=start_pos + fmt.end,
                        style=fmt.style,
                        metadata=fmt.metadata,
                    )
                )

            current_pos += len(inner_text)

        elif child_type == "codespan":
            # Inline code
            code_text = child.get("raw", "")
            start_pos = current_pos
            text_parts.append(code_text)

            formatting.append(
                TextFormat(
                    start=start_pos, end=start_pos + len(code_text), style=FormatStyle.CODE
                )
            )

            current_pos += len(code_text)

        elif child_type == "link":
            # Link - extract text and append URL in parentheses
            start_pos = current_pos
            inner_text, inner_fmt = extract_text_and_formatting(
                child.get("children", [])
            )
            url = child.get("attrs", {}).get("url", "")

            # Format as "text (URL)"
            link_text = f"{inner_text} ({url})" if url else inner_text
            text_parts.append(link_text)

            # Mark entire link text (including URL) as link formatting
            formatting.append(
                TextFormat(
                    start=start_pos,
                    end=start_pos + len(link_text),
                    style=FormatStyle.LINK,
                    metadata={"url": url},
                )
            )

            # Include nested formatting within the link text part only
            for fmt in inner_fmt:
                formatting.append(
                    TextFormat(
                        start=start_pos + fmt.start,
                        end=start_pos + fmt.end,
                        style=fmt.style,
                        metadata=fmt.metadata,
                    )
                )

            current_pos += len(link_text)

        elif child_type == "image":
            # Image - replace with placeholder
            alt_text = child.get("attrs", {}).get("alt", "Image")
            placeholder = f"[Image: {alt_text}]"
            text_parts.append(placeholder)
            current_pos += len(placeholder)

        elif child_type == "strikethrough":
            # Strikethrough text
            start_pos = current_pos
            inner_text, inner_fmt = extract_text_and_formatting(
                child.get("children", [])
            )
            text_parts.append(inner_text)

            formatting.append(
                TextFormat(
                    start=start_pos,
                    end=start_pos + len(inner_text),
                    style=FormatStyle.STRIKETHROUGH,
                )
            )

            for fmt in inner_fmt:
                formatting.append(
                    TextFormat(
                        start=start_pos + fmt.start,
                        end=start_pos + fmt.end,
                        style=fmt.style,
                        metadata=fmt.metadata,
                    )
                )

            current_pos += len(inner_text)

        elif child_type == "linebreak" or child_type == "softbreak":
            # Line breaks
            text_parts.append(" ")
            current_pos += 1

        else:
            # Unknown inline type - log and skip
            logger.debug(f"Unhandled inline node type: {child_type}")

    return "".join(text_parts), formatting


def visualize_formatting(text: str, formatting: list[TextFormat]) -> str:
    """Create a visual representation of text with formatting markers.

    This is a debugging/testing helper to verify formatting positions are correct.

    Args:
        text: Plain text
        formatting: List of formatting ranges

    Returns:
        Multi-line string showing text with formatting markers

    Example:
        >>> text = "This is bold and italic text"
        >>> fmt = [TextFormat(8, 12, BOLD), TextFormat(17, 23, ITALIC)]
        >>> print(visualize_formatting(text, fmt))
        This is bold and italic text
                ^^^^         ^^^^^^
                BOLD         ITALIC
    """
    if not formatting:
        return text

    lines = [text]

    # Sort formatting by start position
    sorted_fmt = sorted(formatting, key=lambda f: f.start)

    for fmt in sorted_fmt:
        # Create marker line
        marker = " " * fmt.start + "^" * (fmt.end - fmt.start)
        label = " " * fmt.start + fmt.style.value.upper()

        # Find appropriate line to place this marker
        # For simplicity, just add new lines
        lines.append(marker)
        lines.append(label)

    return "\n".join(lines)
