"""Content pagination with paragraph splitting support.

This module provides a single source of truth for block-based pagination logic.
It is used by both RemarkableGenerator and DocumentModel to ensure consistent
pagination behavior across the codebase.

The key algorithm:
1. Estimate line count for each block
2. Fill pages until lines_per_page is reached
3. Handle header orphan prevention (headers near bottom start new page)
4. Split paragraphs when enabled or when they exceed one full page
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rock_paper_sync.parser import ContentBlock

logger = logging.getLogger(__name__)

# Minimum lines remaining on page before starting header on new page
HEADER_ORPHAN_THRESHOLD_LINES = 10


class ContentPaginator:
    """Paginates ContentBlocks into pages with paragraph splitting support.

    This class encapsulates the pagination algorithm used across the codebase.
    It handles:
    - Line-based page breaks
    - Header orphan prevention
    - Paragraph splitting (optional or forced for oversized)

    Example:
        from rock_paper_sync.layout import WordWrapLayoutEngine, ContentPaginator

        engine = WordWrapLayoutEngine.from_geometry(geometry)
        paginator = ContentPaginator(
            layout_engine=engine,
            lines_per_page=28,
            allow_paragraph_splitting=True,
        )
        pages = paginator.paginate(blocks)
    """

    def __init__(
        self,
        layout_engine,
        lines_per_page: int,
        allow_paragraph_splitting: bool = False,
        estimate_block_lines: Callable[[ContentBlock], int] | None = None,
    ):
        """Initialize the paginator.

        Args:
            layout_engine: WordWrapLayoutEngine for text measurements
            lines_per_page: Maximum lines per page
            allow_paragraph_splitting: If True, split paragraphs to fill pages.
                If False, paragraphs are kept atomic (except when oversized).
                Paragraphs exceeding one full page are ALWAYS split.
            estimate_block_lines: Optional function to estimate lines for a block.
                If not provided, uses default estimation based on layout engine.
        """
        self.layout_engine = layout_engine
        self.lines_per_page = lines_per_page
        self.allow_paragraph_splitting = allow_paragraph_splitting
        self._estimate_block_lines = estimate_block_lines

    def paginate(self, blocks: list[ContentBlock]) -> list[list[ContentBlock]]:
        """Split content blocks into pages.

        Args:
            blocks: List of ContentBlocks to paginate

        Returns:
            List of pages, where each page is a list of ContentBlocks.
            Empty input returns one empty page: [[]]
        """
        from rock_paper_sync.parser import BlockType

        if not blocks:
            return [[]]

        pages: list[list[ContentBlock]] = []
        current_page: list[ContentBlock] = []
        current_lines = 0

        for block in blocks:
            block_lines = self._get_block_lines(block)

            # Header orphan prevention: headers near bottom start new page
            if block.type == BlockType.HEADER and current_page:
                remaining_space = self.lines_per_page - current_lines
                if remaining_space < HEADER_ORPHAN_THRESHOLD_LINES:
                    pages.append(current_page)
                    current_page = []
                    current_lines = 0

            # Check if block fits on current page
            if current_lines + block_lines > self.lines_per_page:
                # Block doesn't fit on current page
                is_paragraph = block.type == BlockType.PARAGRAPH
                is_oversized = block_lines > self.lines_per_page
                should_split = is_paragraph and (self.allow_paragraph_splitting or is_oversized)

                if should_split:
                    self._split_paragraph(
                        block,
                        current_page,
                        current_lines,
                        pages,
                    )
                    # _split_paragraph modifies current_page in place with the last chunk
                    # Just update current_lines to match
                    current_lines = sum(self._get_block_lines(b) for b in current_page)
                elif current_page:
                    # Atomic block placement - start new page
                    pages.append(current_page)
                    current_page = [block]
                    current_lines = block_lines
                else:
                    # First block on empty page
                    current_page.append(block)
                    current_lines = block_lines
            else:
                current_page.append(block)
                current_lines += block_lines

        # Don't forget the last page
        if current_page:
            pages.append(current_page)

        return pages if pages else [[]]

    def _split_paragraph(
        self,
        block: ContentBlock,
        current_page: list[ContentBlock],
        current_lines: int,
        pages: list[list[ContentBlock]],
    ) -> None:
        """Split a paragraph across pages.

        Modifies current_page and pages in place.

        Args:
            block: The paragraph block to split
            current_page: Current page being built (modified in place)
            current_lines: Current line count on page
            pages: List of completed pages (modified in place)
        """
        from rock_paper_sync.parser import ContentBlock

        remaining_lines = self.lines_per_page - current_lines

        if self.allow_paragraph_splitting and remaining_lines > 0 and current_page:
            # Fill remaining space on current page, then full pages
            chunks = self.layout_engine.split_for_pages(
                block.text,
                self.lines_per_page,
                first_chunk_lines=remaining_lines,
            )
        else:
            # Forced split (oversized) or no space - start on new page
            if current_page:
                pages.append(list(current_page))  # Copy before clearing
                current_page.clear()
            chunks = self.layout_engine.split_for_pages(block.text, self.lines_per_page)

        for i, chunk_text in enumerate(chunks):
            # Start new page after first chunk
            if i > 0 and current_page:
                pages.append(list(current_page))
                current_page.clear()

            chunk_block = ContentBlock(
                type=block.type,
                level=block.level,
                text=chunk_text,
                formatting=block.formatting if i == 0 else [],
                page_index=len(pages),
            )
            current_page.append(chunk_block)

    def _get_block_lines(self, block: ContentBlock) -> int:
        """Get estimated line count for a block.

        Args:
            block: ContentBlock to estimate

        Returns:
            Estimated number of lines
        """
        if self._estimate_block_lines:
            return self._estimate_block_lines(block)

        # Default estimation using layout engine
        from rock_paper_sync.parser import BlockType

        if block.type == BlockType.HORIZONTAL_RULE:
            return 2

        text = block.text
        if block.type == BlockType.LIST_ITEM:
            text = f"• {text}"

        # Code blocks: count actual newlines + padding
        if block.type == BlockType.CODE_BLOCK:
            return text.count("\n") + 2

        line_breaks = self.layout_engine.calculate_line_breaks(text, self.layout_engine.text_width)
        lines = len(line_breaks)

        # Headers get extra spacing
        if block.type == BlockType.HEADER:
            lines += 1

        return max(1, lines)
