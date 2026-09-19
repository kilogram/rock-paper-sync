"""Mistune AST to tokenised HTML converter.

Converts the parser's ``ContentBlock`` list into an HTML string where
every whitespace-separated word, list marker, code token, and heading
word is wrapped in ``<span data-token="N">`` in strict reading order.

The token index N is a zero-based integer that becomes the ``TokenId``
in the word map.  WeasyPrint lays these spans out; the PDF renderer
walks the box tree to find each token's screen position.

Special handling:
    - ``==text==`` (mistune ``mark`` plugin) renders as ``<mark>``.
    - Task-list checkboxes (``- [ ]`` / ``- [x]``) render as
      CSS-bordered inline boxes inside a token span.
    - Trailing blank pages are appended as ruled background divs.
    - RPS marker comments are stripped before rendering.

No imports of weasyprint, fitz, sync_v3, or rm_cloud_sync.
"""

from __future__ import annotations

import html as html_mod
import re

from rock_paper_sync.parser import BlockType, ContentBlock, FormatStyle

__all__ = ["ast_to_html", "TOKEN_ATTR"]

TOKEN_ATTR = "data-token"

# Checkbox patterns in list item text (prepended by the parser)
_CHECKBOX_UNCHECKED = "[ ] "
_CHECKBOX_CHECKED = "[x] "

# Ordered list number prefix pattern (e.g. "1. ", "12. ")
_ORDERED_PREFIX_RE = re.compile(r"^(\d+)\. ")

# RPS marker comment pattern (strip before rendering)
_RPS_MARKER_RE = re.compile(
    r"<!--\s*RPS:.*?-->.*?<!--\s*RPS:END\s*-->",
    re.DOTALL,
)
_RPS_ANNOTATED_RE = re.compile(r"<!--\s*ANNOTATED:.*?-->", re.DOTALL)
_RPS_END_RE = re.compile(r"<!--\s*/ANNOTATED\s*-->", re.DOTALL)


def _strip_rps_markers(text: str) -> str:
    """Remove RPS marker comments from text."""
    text = _RPS_MARKER_RE.sub("", text)
    text = _RPS_ANNOTATED_RE.sub("", text)
    text = _RPS_END_RE.sub("", text)
    return text.strip()


def _tokenise_text(text: str) -> list[str]:
    """Split text into whitespace-separated tokens, preserving whitespace as separators."""
    if not text:
        return []
    # Split on whitespace but keep the tokens
    return text.split()


def _wrap_token(token_idx: int, content: str) -> str:
    """Wrap content in a data-token span."""
    return f'<span {TOKEN_ATTR}="{token_idx}">{content}</span>'


def _apply_inline_formatting(
    text: str,
    formatting: list,  # list[TextFormat]
) -> str:
    """Apply inline formatting (bold, italic, etc.) to text.

    Returns HTML with formatting tags applied.
    """
    if not formatting:
        return html_mod.escape(text)

    # Build a character-level markup map
    chars = list(text)
    # Track open/close tags at each position
    opens: dict[int, list[str]] = {}
    closes: dict[int, list[str]] = {}

    for fmt in formatting:
        start = max(0, fmt.start)
        end = min(len(chars), fmt.end)
        if start >= end:
            continue

        if fmt.style == FormatStyle.BOLD:
            opens.setdefault(start, []).append("<strong>")
            closes.setdefault(end, []).append("</strong>")
        elif fmt.style == FormatStyle.ITALIC:
            opens.setdefault(start, []).append("<em>")
            closes.setdefault(end, []).append("</em>")
        elif fmt.style == FormatStyle.CODE:
            opens.setdefault(start, []).append("<code>")
            closes.setdefault(end, []).append("</code>")
        elif fmt.style == FormatStyle.STRIKETHROUGH:
            opens.setdefault(start, []).append("<del>")
            closes.setdefault(end, []).append("</del>")
        elif fmt.style == FormatStyle.MARK:
            opens.setdefault(start, []).append("<mark>")
            closes.setdefault(end, []).append("</mark>")
        elif fmt.style == FormatStyle.LINK:
            opens.setdefault(start, []).append('<span class="rps-link">')
            closes.setdefault(end, []).append("</span>")

    result: list[str] = []
    for i, ch in enumerate(chars):
        # Close tags first (LIFO order for proper nesting)
        if i in closes:
            for tag in reversed(closes[i]):
                result.append(tag)
        if i in opens:
            for tag in opens[i]:
                result.append(tag)
        result.append(html_mod.escape(ch))

    # Close any remaining tags
    end_pos = len(chars)
    if end_pos in closes:
        for tag in reversed(closes[end_pos]):
            result.append(tag)

    return "".join(result)


def _shift_formatting(formatting: list, offset: int) -> list:
    """Shift formatting offsets left by `offset`, dropping entries that end before the new start."""
    return [
        type(f)(
            start=f.start - offset,
            end=f.end - offset,
            style=f.style,
            metadata=f.metadata,
        )
        for f in formatting
        if f.end > offset
    ]


def _render_block_tokens(
    text: str,
    formatting: list,
    token_counter: list[int],  # mutable counter [current_index]
    block_type: BlockType,
    level: int = 0,
) -> str:
    """Render a block's text as tokenised HTML spans.

    Each whitespace-separated word gets its own data-token span.
    Formatting is applied within each token's span.
    """
    parts: list[str] = []

    # Handle special prefixes for list items (checkbox, ordered number).
    # These are stripped because the marker is rendered separately by ast_to_html.
    prefix_html = ""
    remaining_text = text
    remaining_fmt = formatting

    if block_type == BlockType.LIST_ITEM:
        if text.startswith(_CHECKBOX_CHECKED):
            prefix_html = (
                f'<span {TOKEN_ATTR}="{token_counter[0]}">'
                '<span class="rps-checkbox rps-checkbox-checked">x</span>'
                "</span> "
            )
            token_counter[0] += 1
            remaining_text = text[len(_CHECKBOX_CHECKED) :]
            remaining_fmt = _shift_formatting(formatting, len(_CHECKBOX_CHECKED))
        elif text.startswith(_CHECKBOX_UNCHECKED):
            prefix_html = (
                f'<span {TOKEN_ATTR}="{token_counter[0]}">'
                '<span class="rps-checkbox"></span>'
                "</span> "
            )
            token_counter[0] += 1
            remaining_text = text[len(_CHECKBOX_UNCHECKED) :]
            remaining_fmt = _shift_formatting(formatting, len(_CHECKBOX_UNCHECKED))
        else:
            # Strip ordered-number prefix ("N. ") — rendered as marker by ast_to_html
            m = _ORDERED_PREFIX_RE.match(text)
            if m:
                prefix_len = m.end()
                remaining_text = text[prefix_len:]
                remaining_fmt = _shift_formatting(formatting, prefix_len)

    if prefix_html:
        parts.append(prefix_html)

    tokens = _tokenise_text(remaining_text)
    if not tokens:
        return "".join(parts)

    # Map each token back to its position in the original text
    pos = 0
    for i, tok in enumerate(tokens):
        # Find this token's position in remaining_text
        idx = remaining_text.find(tok, pos)
        if idx < 0:
            idx = pos  # fallback

        # Collect formatting that intersects this token
        tok_start = idx
        tok_end = idx + len(tok)
        tok_formatting = [
            type(f)(
                start=max(0, f.start - tok_start),
                end=min(len(tok), f.end - tok_start),
                style=f.style,
                metadata=f.metadata,
            )
            for f in remaining_fmt
            if f.start < tok_end and f.end > tok_start
        ]

        formatted_content = _apply_inline_formatting(tok, tok_formatting)
        parts.append(_wrap_token(token_counter[0], formatted_content))
        token_counter[0] += 1

        pos = tok_end

        # Add space between tokens (not after last)
        if i < len(tokens) - 1:
            parts.append(" ")

    return "".join(parts)


def ast_to_html(
    blocks: list[ContentBlock],
    trailing_blank_pages: int = 0,
    page_height_pt: float = 0.0,
) -> tuple[str, int]:
    """Convert parsed content blocks to tokenised HTML.

    Args:
        blocks: Parsed content blocks from the parser.
        trailing_blank_pages: Number of ruled blank pages to append.
        page_height_pt: Page height in points (for trailing pages CSS).

    Returns:
        Tuple of (html_string, total_token_count).
    """
    token_counter = [0]  # mutable counter passed through
    body_parts: list[str] = []

    for block in blocks:
        text = _strip_rps_markers(block.text) if block.text else ""
        if not text and block.type not in (BlockType.HORIZONTAL_RULE,):
            continue

        if block.type == BlockType.HEADER:
            tag = f"h{min(block.level, 6)}"
            content = _render_block_tokens(
                text, block.formatting, token_counter, block.type, block.level
            )
            body_parts.append(f"<{tag}>{content}</{tag}>")

        elif block.type == BlockType.PARAGRAPH:
            content = _render_block_tokens(text, block.formatting, token_counter, block.type)
            body_parts.append(f"<p>{content}</p>")

        elif block.type == BlockType.LIST_ITEM:
            indent_class = f" rps-indent-{block.level}" if block.level > 1 else ""

            # Detect ordered-number prefix ("N. ") from parser
            ordered_match = _ORDERED_PREFIX_RE.match(text)
            if ordered_match:
                marker = f"{ordered_match.group(1)}."
            else:
                marker = "•"  # bullet

            # The marker is a token in the word map.
            marker_html = _wrap_token(token_counter[0], html_mod.escape(marker))
            token_counter[0] += 1

            content = _render_block_tokens(
                text, block.formatting, token_counter, block.type, block.level
            )
            body_parts.append(
                f'<div class="rps-list-item{indent_class}">'
                f'<span class="rps-marker">{marker_html}</span> {content}'
                f"</div>"
            )

        elif block.type == BlockType.CODE_BLOCK:
            # Code blocks: each line is a token
            lines = text.split("\n")
            code_lines: list[str] = []
            for line in lines:
                if line.strip():
                    line_html = _wrap_token(token_counter[0], html_mod.escape(line))
                    token_counter[0] += 1
                    code_lines.append(line_html)
                else:
                    code_lines.append("")
            body_parts.append(f'<pre class="rps-code">{chr(10).join(code_lines)}</pre>')

        elif block.type == BlockType.BLOCKQUOTE:
            content = _render_block_tokens(text, block.formatting, token_counter, block.type)
            body_parts.append(f"<blockquote>{content}</blockquote>")

        elif block.type == BlockType.HORIZONTAL_RULE:
            body_parts.append("<hr>")

    # Append trailing blank pages
    for _ in range(trailing_blank_pages):
        body_parts.append('<div class="rps-blank-page"></div>')

    total_tokens = token_counter[0]
    body_html = "\n".join(body_parts)
    return body_html, total_tokens
