"""reMarkable document generator with rmscene integration.

This module converts parsed markdown documents into reMarkable v6 format files.
It handles pagination, text positioning, and generates binary .rm files using
the rmscene library.
"""

import io
import json
import logging
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import rmscene
from rmscene import scene_items as si
from rmscene.crdt_sequence import CrdtId, CrdtSequence, CrdtSequenceItem
from rmscene.scene_stream import (
    AuthorIdsBlock,
    MigrationInfoBlock,
    PageInfoBlock,
    RootTextBlock,
    SceneGroupItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)
from rmscene.tagged_block_common import LwwValue

from .config import LayoutConfig
from .parser import BlockType, ContentBlock, FormatStyle, MarkdownDocument, TextFormat

logger = logging.getLogger("rock_paper_sync.generator")


@dataclass
class TextItem:
    """A positioned text element on a page.

    Attributes:
        text: Plain text content
        x: X position in pixels (from left edge)
        y: Y position in pixels (from top edge)
        width: Text box width in pixels
        formatting: List of inline formatting ranges
    """

    text: str
    x: float
    y: float
    width: float
    formatting: list[TextFormat] = field(default_factory=list)


@dataclass
class RemarkablePage:
    """A single page in a reMarkable document.

    Attributes:
        uuid: Unique page identifier
        text_items: List of positioned text items on this page
    """

    uuid: str
    text_items: list[TextItem] = field(default_factory=list)


@dataclass
class RemarkableDocument:
    """A complete reMarkable document with pages.

    Attributes:
        uuid: Unique document identifier
        visible_name: Display name in reMarkable UI
        parent_uuid: Parent folder UUID (empty for root)
        pages: List of document pages
        modified_time: Last modification timestamp (milliseconds)
    """

    uuid: str
    visible_name: str
    parent_uuid: str
    pages: list[RemarkablePage]
    modified_time: int


class RemarkableGenerator:
    """Generates reMarkable v6 format documents from parsed markdown.

    This generator converts markdown content blocks into reMarkable pages with
    properly positioned text items. It uses the rmscene library to create
    binary .rm files compatible with reMarkable firmware v3.0+.

    Pagination Algorithm
    --------------------

    The pagination algorithm breaks markdown content into pages by estimating
    line counts and applying smart breaking rules:

    1. **Line Estimation**: Calculate lines per block based on:
       - Text length and available width (considering margins)
       - Block type (headers get extra spacing, code blocks count newlines)
       - List item indentation and bullets

    2. **Page Breaking Rules**:
       - Never exceed lines_per_page (default: 28 lines)
       - By default: Never split blocks mid-way (atomic block placement)
       - If allow_paragraph_splitting=True: Fill pages by splitting paragraphs
       - Headers near page bottom (< 10 lines remaining) start new page
       - Prevents orphan headers at bottom of pages

    3. **Text Positioning**:
       - Y position accumulates from margin_top
       - X position respects margin_left plus list indentation
       - Text width accounts for right margin
       - List items get 20px indent per nesting level

    4. **rmscene Integration**:
       - Custom scene tree construction with optimized text width (750px)
       - Ensures 1.0x display zoom on Paper Pro (vs 0.8x with default 936px)
       - Combines all text items with newlines (Phase 1 simplification)
       - Future: Multiple Text scene items for precise positioning

    Attributes:
        layout: Page layout configuration
        page_width: Page width in pixels (1404 for reMarkable Paper Pro)
        page_height: Page height in pixels (1872 for reMarkable Paper Pro)
        line_height: Approximate pixels per line (35px)
        char_width: Pixels per character (15px, measured from device)
    """

    # reMarkable Paper Pro dimensions
    PAGE_WIDTH = 1404
    PAGE_HEIGHT = 1872
    LINE_HEIGHT = 35  # Approximate pixels per line
    CHAR_WIDTH = 15  # Pixels per character (measured: 50-51 chars/line max)

    # Text area dimensions for 1.0x display (optimized for Paper Pro)
    TEXT_WIDTH = 750.0  # Width that displays at 1.0x zoom
    TEXT_POS_X = -375.0  # Centered: -TEXT_WIDTH/2
    TEXT_POS_Y = 94.0   # Top margin: ~2 lines worth (was 234.0 = 5 lines)

    # Calculated default lines per page based on actual device measurements
    # Visible lines on Paper Pro: 26 lines
    # Conservative default with safety margin: 28 lines
    # Total height: 94 (top) + 980 (content) + 94 (bottom) = 1168px
    DEFAULT_LINES_PER_PAGE = 28

    def __init__(self, layout_config: LayoutConfig) -> None:
        """Initialize generator with layout settings.

        Args:
            layout_config: Page layout configuration
        """
        self.layout = layout_config
        self.page_width = self.PAGE_WIDTH
        self.page_height = self.PAGE_HEIGHT
        self.line_height = self.LINE_HEIGHT
        self.char_width = self.CHAR_WIDTH

        logger.info("RemarkableGenerator initialized with rmscene integration")

    def generate_document(
        self,
        md_doc: MarkdownDocument,
        parent_uuid: str = "",
        doc_uuid: str | None = None,
        existing_page_uuids: list[str] | None = None,
    ) -> RemarkableDocument:
        """Convert markdown document to reMarkable format.

        Args:
            md_doc: Parsed markdown document
            parent_uuid: UUID of parent folder (empty for root)
            doc_uuid: Existing document UUID to reuse (for updates), or None for new documents
            existing_page_uuids: Existing page UUIDs to reuse (avoids CRDT conflicts on updates)

        Returns:
            RemarkableDocument ready to be written to disk

        Note:
            When doc_uuid is provided (update case), the existing document will be
            overwritten, including any annotations the user made on the device.
            If existing_page_uuids is provided, those page UUIDs will be reused to avoid
            CRDT merge conflicts.
        """
        # Reuse existing UUID for updates, or generate new one for new documents
        doc_uuid = doc_uuid or str(uuid_module.uuid4())
        timestamp = int(time.time() * 1000)
        existing_page_uuids = existing_page_uuids or []

        # Paginate content blocks
        page_blocks = self.paginate_content(md_doc.content)

        # Generate pages with positioned text items
        pages: list[RemarkablePage] = []
        for i, blocks in enumerate(page_blocks):
            # Reuse existing page UUID if available, otherwise generate new one
            if i < len(existing_page_uuids):
                page_uuid = existing_page_uuids[i]
                logger.debug(f"Reusing existing page UUID: {page_uuid}")
            else:
                page_uuid = str(uuid_module.uuid4())
                logger.debug(f"Generated new page UUID: {page_uuid}")

            text_items = self.blocks_to_text_items(blocks)
            pages.append(RemarkablePage(uuid=page_uuid, text_items=text_items))

        logger.debug(
            f"Generated document {doc_uuid} with {len(pages)} page(s) "
            f"from {len(md_doc.content)} content blocks"
        )

        return RemarkableDocument(
            uuid=doc_uuid,
            visible_name=md_doc.title,
            parent_uuid=parent_uuid,
            pages=pages,
            modified_time=timestamp,
        )

    def paginate_content(
        self, blocks: list[ContentBlock]
    ) -> list[list[ContentBlock]]:
        """Split content blocks into pages based on line count.

        This method estimates how many lines each block will take and breaks
        content into pages that fit within the configured lines_per_page limit.

        Args:
            blocks: List of content blocks to paginate

        Returns:
            List of pages, where each page is a list of content blocks

        Note:
            - Headers near the bottom of a page start a new page
            - Blocks are never split mid-way
            - Empty content results in one empty page
        """
        if not blocks:
            # At least one empty page
            return [[]]

        pages: list[list[ContentBlock]] = []
        current_page: list[ContentBlock] = []
        current_lines = 0

        for block in blocks:
            block_lines = self.estimate_block_lines(block)

            # Check if header should start new page (avoid orphan headers)
            if block.type == BlockType.HEADER and current_page:
                remaining_space = self.layout.lines_per_page - current_lines
                if remaining_space < 10:  # Less than 10 lines remaining
                    pages.append(current_page)
                    current_page = []
                    current_lines = 0

            # Check if block fits on current page
            if current_lines + block_lines > self.layout.lines_per_page:
                # Block doesn't fit - either split it or start new page
                if self.layout.allow_paragraph_splitting and block.type == BlockType.PARAGRAPH:
                    # Split paragraph across pages
                    lines_available = self.layout.lines_per_page - current_lines
                    chars_per_line = max(1, int(self.TEXT_WIDTH / self.CHAR_WIDTH))

                    # Estimate chars that fit on current page
                    chars_for_current = lines_available * chars_per_line

                    if chars_for_current > 0 and len(block.text) > chars_for_current:
                        # Split the text (try to split at word boundary)
                        split_point = chars_for_current
                        # Try to find a space near the split point
                        space_before = block.text.rfind(' ', 0, split_point)
                        if space_before > chars_for_current * 0.8:  # Within 20% of target
                            split_point = space_before

                        # Create two blocks from the split
                        current_text = block.text[:split_point].rstrip()
                        remaining_text = block.text[split_point:].lstrip()

                        if current_text:
                            current_block = ContentBlock(
                                type=block.type,
                                level=block.level,
                                text=current_text,
                                formatting=block.formatting,  # Note: formatting may not be accurate across split
                            )
                            current_page.append(current_block)

                        if current_page:
                            pages.append(current_page)

                        # Start new page with remaining text
                        if remaining_text:
                            next_block = ContentBlock(
                                type=block.type,
                                level=block.level,
                                text=remaining_text,
                                formatting=[],  # Formatting lost across split for now
                            )
                            current_page = [next_block]
                            current_lines = self.estimate_block_lines(next_block)
                        else:
                            current_page = []
                            current_lines = 0
                    else:
                        # Not enough room to split meaningfully, start new page
                        if current_page:
                            pages.append(current_page)
                        current_page = [block]
                        current_lines = block_lines
                else:
                    # Atomic block placement (default behavior)
                    if current_page:
                        pages.append(current_page)
                    current_page = [block]
                    current_lines = block_lines
            else:
                current_page.append(block)
                current_lines += block_lines

        # Don't forget the last page
        if current_page:
            pages.append(current_page)

        logger.info(
            f"Paginated {len(blocks)} blocks into {len(pages)} page(s), "
            f"target lines per page: {self.layout.lines_per_page}"
        )
        for i, page_blocks in enumerate(pages, 1):
            total_lines = sum(self.estimate_block_lines(block) for block in page_blocks)
            logger.info(f"  Page {i}: {len(page_blocks)} blocks, {total_lines} estimated lines")

        return pages if pages else [[]]

    def estimate_block_lines(self, block: ContentBlock) -> int:
        """Estimate how many lines a content block will occupy.

        Args:
            block: Content block to estimate

        Returns:
            Estimated number of lines
        """
        if block.type == BlockType.HORIZONTAL_RULE:
            return 2

        # Calculate based on text length and actual text width
        # Use TEXT_WIDTH instead of page margins for accurate pagination
        chars_per_line = max(1, int(self.TEXT_WIDTH / self.char_width))

        # Account for list item bullet
        text = block.text
        if block.type == BlockType.LIST_ITEM:
            text = f"• {text}"

        # Calculate wrapped lines (+1 accounts for partial last line)
        text_lines = max(1, len(text) // chars_per_line + 1)

        # No extra spacing for paragraphs (spacing handled by blank lines in markdown)
        if block.type == BlockType.HEADER:
            result = text_lines + 1  # Extra space after header for readability
        elif block.type == BlockType.PARAGRAPH:
            result = text_lines  # No extra spacing
        elif block.type == BlockType.CODE_BLOCK:
            # Code blocks: count actual newlines
            result = text.count("\n") + 2
        else:
            result = text_lines

        return result

    def blocks_to_text_items(self, blocks: list[ContentBlock]) -> list[TextItem]:
        """Convert content blocks to positioned text items.

        Each block is positioned on the page based on the running Y position
        and the configured margins.

        Args:
            blocks: Content blocks for a single page

        Returns:
            List of positioned text items
        """
        items: list[TextItem] = []
        y_position = float(self.layout.margin_top)

        for block in blocks:
            if block.type == BlockType.HORIZONTAL_RULE:
                # Skip horizontal rules (not rendered as text in Phase 1)
                y_position += self.line_height * 2
                continue

            x_position = float(self.layout.margin_left)
            width = float(
                self.page_width - self.layout.margin_left - self.layout.margin_right
            )

            # Prepare text with list bullet if needed
            text = block.text
            if block.type == BlockType.LIST_ITEM:
                # Add bullet and indentation for lists
                indent = 20 * block.level
                x_position += indent
                width -= indent
                text = f"• {text}"

            # Create text item
            items.append(
                TextItem(
                    text=text,
                    x=x_position,
                    y=y_position,
                    width=width,
                    formatting=block.formatting,
                )
            )

            # Update Y position for next block
            lines = self.estimate_block_lines(block)
            y_position += lines * self.line_height

        return items

    def generate_rm_file(self, page: RemarkablePage) -> bytes:
        """Generate binary .rm file content with custom text width.

        This creates a reMarkable v6 format file with text content optimized
        for 1.0x display on the Paper Pro by using a custom text width instead
        of the default from simple_text_document().

        Args:
            page: RemarkablePage with positioned text items

        Returns:
            Binary .rm file content

        Note:
            Uses custom scene tree construction to set text width to 750px,
            which displays at 1.0x zoom on the Paper Pro (vs 0.8x with the
            default 936px width from simple_text_document).
            Inline formatting (bold/italic) is preserved in the text but
            not visually rendered due to rmscene/reMarkable limitations.
        """
        # Combine all text items into a single text block
        combined_text = "\n".join(item.text for item in page.text_items)

        if not combined_text.strip():
            combined_text = " "  # At least one space for empty pages

        # Generate blocks manually with custom text width
        author_uuid = uuid4()

        blocks = [
            AuthorIdsBlock(author_uuids={1: author_uuid}),
            MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
            PageInfoBlock(
                loads_count=1,
                merges_count=0,
                text_chars_count=len(combined_text) + 1,
                text_lines_count=combined_text.count("\n") + 1,
            ),
            SceneTreeBlock(
                tree_id=CrdtId(0, 11),
                node_id=CrdtId(0, 0),
                is_update=True,
                parent_id=CrdtId(0, 1),
            ),
            RootTextBlock(
                block_id=CrdtId(0, 0),
                value=si.Text(
                    items=CrdtSequence(
                        [
                            CrdtSequenceItem(
                                item_id=CrdtId(1, 16),
                                left_id=CrdtId(0, 0),
                                right_id=CrdtId(0, 0),
                                deleted_length=0,
                                value=combined_text,
                            )
                        ]
                    ),
                    styles={
                        CrdtId(0, 0): LwwValue(
                            timestamp=CrdtId(1, 15), value=si.ParagraphStyle.PLAIN
                        ),
                    },
                    pos_x=self.TEXT_POS_X,
                    pos_y=self.TEXT_POS_Y,
                    width=self.TEXT_WIDTH,
                ),
            ),
            TreeNodeBlock(
                si.Group(
                    node_id=CrdtId(0, 1),
                )
            ),
            TreeNodeBlock(
                si.Group(
                    node_id=CrdtId(0, 11),
                    label=LwwValue(timestamp=CrdtId(0, 12), value="Layer 1"),
                )
            ),
            SceneGroupItemBlock(
                parent_id=CrdtId(0, 1),
                item=CrdtSequenceItem(
                    item_id=CrdtId(0, 13),
                    left_id=CrdtId(0, 0),
                    right_id=CrdtId(0, 0),
                    deleted_length=0,
                    value=CrdtId(0, 11),
                ),
            ),
        ]

        # Serialize to binary format
        buffer = io.BytesIO()
        rmscene.write_blocks(buffer, blocks)
        rm_bytes = buffer.getvalue()

        logger.debug(
            f"Generated .rm file: {len(rm_bytes)} bytes, "
            f"{len(page.text_items)} text items, "
            f"{len(combined_text)} characters"
        )

        return rm_bytes

