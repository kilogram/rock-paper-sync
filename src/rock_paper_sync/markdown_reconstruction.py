"""Reconstruct markdown text from parsed ContentBlock structures.

This module provides utilities to convert ContentBlock objects back into
markdown source text. Used primarily for adding annotation markers at the
correct block boundaries.

The reconstruction is **semantic** not **exact** - the output markdown will
render the same but may have different source formatting than the original.

Example:
    >>> from parser import parse_markdown_file, ContentBlock, BlockType
    >>> doc = parse_markdown_file(Path("document.md"))
    >>> markdown_text = blocks_to_markdown(doc.content)
    >>> # markdown_text renders identically to original
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .parser import ContentBlock, BlockType, FormatStyle


def block_to_markdown(block: 'ContentBlock', indent_level: int = 0) -> str:
    """Convert a single ContentBlock to markdown text.

    Args:
        block: ContentBlock to convert
        indent_level: Indentation level for nested blocks (lists, blockquotes)

    Returns:
        Markdown text representation of the block

    Example:
        >>> from parser import ContentBlock, BlockType
        >>> block = ContentBlock(type=BlockType.HEADER, level=1, text="Title")
        >>> block_to_markdown(block)
        '# Title'
    """
    from .parser import BlockType

    indent = "  " * indent_level

    if block.type == BlockType.HEADER:
        prefix = "#" * block.level
        return f"{prefix} {block.text}"

    elif block.type == BlockType.LIST_ITEM:
        # Calculate actual indent based on nesting
        item_indent = "  " * (block.level - 1)
        text = apply_inline_formatting(block.text, block.formatting)
        result = f"{item_indent}- {text}"

        # Add nested children
        if block.children:
            children_text = blocks_to_markdown(block.children, block.level)
            result += "\n" + children_text

        return result

    elif block.type == BlockType.CODE_BLOCK:
        # Preserve code blocks as-is
        return f"```\n{block.text}\n```"

    elif block.type == BlockType.BLOCKQUOTE:
        text = apply_inline_formatting(block.text, block.formatting)
        lines = text.split("\n")
        quoted_lines = [f"{indent}> {line}" for line in lines]
        result = "\n".join(quoted_lines)

        # Add nested children
        if block.children:
            children_text = blocks_to_markdown(block.children, indent_level + 1)
            result += "\n" + children_text

        return result

    elif block.type == BlockType.HORIZONTAL_RULE:
        return "---"

    else:  # PARAGRAPH (default)
        text = apply_inline_formatting(block.text, block.formatting)
        return text


def apply_inline_formatting(text: str, formatting: list) -> str:
    """Apply inline formatting (bold, italic, code) to text.

    Args:
        text: Plain text
        formatting: List of TextFormat objects

    Returns:
        Text with markdown formatting applied

    Example:
        >>> from parser import TextFormat, FormatStyle
        >>> text = "Hello world"
        >>> formatting = [TextFormat(start=0, end=5, style=FormatStyle.BOLD)]
        >>> apply_inline_formatting(text, formatting)
        '**Hello** world'
    """
    if not formatting:
        return text

    from .parser import FormatStyle

    # Sort formatting by start position
    sorted_fmt = sorted(formatting, key=lambda f: (f.start, -f.end))

    # Build result with formatting markers
    result = []
    pos = 0

    for fmt in sorted_fmt:
        # Add text before this formatting
        if fmt.start > pos:
            result.append(text[pos:fmt.start])

        # Get formatted segment
        segment = text[fmt.start:fmt.end]

        # Apply formatting markers
        if fmt.style == FormatStyle.BOLD:
            result.append(f"**{segment}**")
        elif fmt.style == FormatStyle.ITALIC:
            result.append(f"*{segment}*")
        elif fmt.style == FormatStyle.CODE:
            result.append(f"`{segment}`")
        elif fmt.style == FormatStyle.STRIKETHROUGH:
            result.append(f"~~{segment}~~")
        elif fmt.style == FormatStyle.LINK:
            # Links have URL in metadata (not implemented yet)
            result.append(f"[{segment}](url)")
        else:
            # Unknown format - add as-is
            result.append(segment)

        pos = fmt.end

    # Add remaining text
    if pos < len(text):
        result.append(text[pos:])

    return ''.join(result)


def blocks_to_markdown(blocks: list['ContentBlock'], indent_level: int = 0) -> str:
    """Convert list of ContentBlocks to markdown text.

    Args:
        blocks: List of ContentBlocks to convert
        indent_level: Base indentation level for nested structures

    Returns:
        Markdown text with blocks separated by blank lines

    Example:
        >>> from parser import parse_markdown_file
        >>> doc = parse_markdown_file(Path("document.md"))
        >>> markdown = blocks_to_markdown(doc.content)
    """
    if not blocks:
        return ""

    markdown_blocks = []

    for block in blocks:
        block_text = block_to_markdown(block, indent_level)
        markdown_blocks.append(block_text)

    # Join blocks with blank lines (standard markdown paragraph separation)
    return "\n\n".join(markdown_blocks)
