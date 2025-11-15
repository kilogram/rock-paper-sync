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

import rmscene

from .config import LayoutConfig
from .metadata import (
    generate_content_metadata,
    generate_document_metadata,
    generate_page_metadata,
)
from .parser import BlockType, ContentBlock, FormatStyle, MarkdownDocument, TextFormat

logger = logging.getLogger("rm_obsidian_sync.generator")


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

    Attributes:
        layout: Page layout configuration
        page_width: Page width in pixels (reMarkable standard)
        page_height: Page height in pixels (reMarkable standard)
        line_height: Approximate pixels per line of text
        char_width: Approximate pixels per character
    """

    # reMarkable Paper Pro dimensions
    PAGE_WIDTH = 1404
    PAGE_HEIGHT = 1872
    LINE_HEIGHT = 35  # Approximate pixels per line
    CHAR_WIDTH = 10  # Approximate pixels per character

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
        self, md_doc: MarkdownDocument, parent_uuid: str = ""
    ) -> RemarkableDocument:
        """Convert markdown document to reMarkable format.

        Args:
            md_doc: Parsed markdown document
            parent_uuid: UUID of parent folder (empty for root)

        Returns:
            RemarkableDocument ready to be written to disk
        """
        doc_uuid = str(uuid_module.uuid4())
        timestamp = int(time.time() * 1000)

        # Paginate content blocks
        page_blocks = self.paginate_content(md_doc.content)

        # Generate pages with positioned text items
        pages: list[RemarkablePage] = []
        for blocks in page_blocks:
            page_uuid = str(uuid_module.uuid4())
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

        logger.debug(
            f"Paginated {len(blocks)} blocks into {len(pages)} page(s), "
            f"target lines per page: {self.layout.lines_per_page}"
        )

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

        # Calculate based on text length and available width
        available_width = (
            self.page_width - self.layout.margin_left - self.layout.margin_right
        )
        chars_per_line = max(1, int(available_width / self.char_width))

        # Account for list item bullet
        text = block.text
        if block.type == BlockType.LIST_ITEM:
            text = f"• {text}"

        text_lines = max(1, len(text) // chars_per_line + 1)

        # Add extra spacing based on block type
        if block.type == BlockType.HEADER:
            return text_lines + 2  # Extra space after header
        elif block.type == BlockType.PARAGRAPH:
            return text_lines + 1  # Space after paragraph
        elif block.type == BlockType.CODE_BLOCK:
            # Code blocks: count actual newlines
            return text.count("\n") + 2
        else:
            return text_lines + 1

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
        """Generate binary .rm file content using rmscene.

        This creates a reMarkable v6 format file with text content.

        Args:
            page: RemarkablePage with positioned text items

        Returns:
            Binary .rm file content

        Note:
            Currently uses rmscene's simple_text_document as a base.
            Inline formatting (bold/italic) is preserved in the text but
            not visually rendered due to rmscene/reMarkable limitations.
        """
        # Combine all text items into a single text block
        # Note: This is a simplified approach for Phase 1
        # Future versions could create multiple Text scene items for positioning
        combined_text = "\n".join(item.text for item in page.text_items)

        if not combined_text.strip():
            combined_text = " "  # At least one space for empty pages

        # Generate blocks using rmscene
        blocks = list(rmscene.simple_text_document(combined_text))

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

    def write_document_files(
        self, doc: RemarkableDocument, output_dir: Path
    ) -> None:
        """Write all document files to output directory.

        Creates the complete file structure required by reMarkable:
        - {uuid}/ directory
        - {uuid}.metadata - Document metadata JSON
        - {uuid}.content - Page list and settings JSON
        - {uuid}.local - Empty JSON object (required for xochitl recognition)
        - {page-uuid}.rm - Binary page content (v6 format)
        - {page-uuid}-metadata.json - Page layer settings

        Args:
            doc: RemarkableDocument to write
            output_dir: Base output directory

        Raises:
            OSError: If file writing fails
        """
        # Create document directory for page files
        doc_dir = output_dir / doc.uuid
        doc_dir.mkdir(parents=True, exist_ok=True)

        # Write .metadata file (at root level, not in subdirectory)
        metadata = generate_document_metadata(
            visible_name=doc.visible_name,
            parent_uuid=doc.parent_uuid,
            modified_time=doc.modified_time,
        )
        (output_dir / f"{doc.uuid}.metadata").write_text(
            json.dumps(metadata, indent=2)
        )

        # Write .content file (at root level, not in subdirectory)
        page_uuids = [page.uuid for page in doc.pages]
        content = generate_content_metadata(page_uuids)
        (output_dir / f"{doc.uuid}.content").write_text(
            json.dumps(content, indent=2)
        )

        # Write .local file (at root level, required by xochitl for document recognition)
        (output_dir / f"{doc.uuid}.local").write_text("{}")

        # Write page files
        for page in doc.pages:
            # Generate and write .rm file
            rm_bytes = self.generate_rm_file(page)
            (doc_dir / f"{page.uuid}.rm").write_bytes(rm_bytes)

            # Write page metadata
            page_meta = generate_page_metadata()
            (doc_dir / f"{page.uuid}-metadata.json").write_text(
                json.dumps(page_meta, indent=2)
            )

        logger.info(
            f"Wrote document {doc.uuid} ({doc.visible_name}) "
            f"with {len(doc.pages)} page(s) to {doc_dir}"
        )
