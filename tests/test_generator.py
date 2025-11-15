"""Tests for reMarkable document generator.

This module tests the generator that converts markdown documents into
reMarkable v6 format files with pagination and rmscene integration.
"""

import json
from pathlib import Path

import pytest
import rmscene

from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.generator import (
    RemarkableDocument,
    RemarkableGenerator,
    RemarkablePage,
    TextItem,
)
from rock_paper_sync.parser import (
    BlockType,
    ContentBlock,
    FormatStyle,
    MarkdownDocument,
    TextFormat,
    parse_markdown_file,
)


@pytest.fixture
def layout_config() -> LayoutConfig:
    """Standard layout configuration for testing."""
    return LayoutConfig(
        lines_per_page=45,
        margin_top=50,
        margin_bottom=50,
        margin_left=50,
        margin_right=50,
    )


@pytest.fixture
def generator(layout_config: LayoutConfig) -> RemarkableGenerator:
    """RemarkableGenerator instance for testing."""
    return RemarkableGenerator(layout_config)


@pytest.fixture
def sample_markdown_doc(tmp_path: Path) -> MarkdownDocument:
    """Create a simple markdown document for testing."""
    md_file = tmp_path / "test.md"
    md_file.write_text(
        """---
title: Test Document
---

# Introduction

This is a test document.

## Section 1

Some content here.

- List item 1
- List item 2
- List item 3

## Section 2

More content.
"""
    )
    return parse_markdown_file(md_file)


@pytest.fixture
def long_markdown_doc(tmp_path: Path) -> MarkdownDocument:
    """Create a long markdown document for pagination testing."""
    md_file = tmp_path / "long.md"
    content = ["# Long Document\n"]

    # Generate enough content for multiple pages (45 lines per page)
    for i in range(30):
        content.append(f"\n## Section {i + 1}\n")
        content.append(
            f"This is paragraph {i + 1}. " * 10
        )  # Long paragraph

    md_file.write_text("".join(content))
    return parse_markdown_file(md_file)


class TestRemarkableGenerator:
    """Tests for RemarkableGenerator class."""

    def test_initialization(self, generator: RemarkableGenerator) -> None:
        """Generator should initialize with correct dimensions."""
        assert generator.page_width == 1404
        assert generator.page_height == 1872
        assert generator.line_height > 0
        assert generator.char_width > 0

    def test_generate_document_basic(
        self, generator: RemarkableGenerator, sample_markdown_doc: MarkdownDocument
    ) -> None:
        """Should generate a basic document."""
        doc = generator.generate_document(sample_markdown_doc)

        assert isinstance(doc, RemarkableDocument)
        assert doc.visible_name == "Test Document"
        assert doc.parent_uuid == ""
        assert len(doc.pages) > 0
        assert doc.modified_time > 0

    def test_generate_document_with_parent(
        self, generator: RemarkableGenerator, sample_markdown_doc: MarkdownDocument
    ) -> None:
        """Should set parent UUID when provided."""
        doc = generator.generate_document(
            sample_markdown_doc, parent_uuid="parent-123"
        )

        assert doc.parent_uuid == "parent-123"

    def test_pages_have_uuids(
        self, generator: RemarkableGenerator, sample_markdown_doc: MarkdownDocument
    ) -> None:
        """Each page should have a unique UUID."""
        doc = generator.generate_document(sample_markdown_doc)

        uuids = [page.uuid for page in doc.pages]
        assert len(uuids) == len(set(uuids))  # All unique
        for uuid in uuids:
            assert len(uuid) > 0  # Non-empty


class TestPagination:
    """Tests for content pagination logic."""

    def test_paginate_empty_content(
        self, generator: RemarkableGenerator
    ) -> None:
        """Empty content should result in one empty page."""
        pages = generator.paginate_content([])

        assert len(pages) == 1
        assert pages[0] == []

    def test_paginate_single_block(
        self, generator: RemarkableGenerator
    ) -> None:
        """Single block should fit on one page."""
        blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text="Short paragraph",
                formatting=[],
            )
        ]

        pages = generator.paginate_content(blocks)

        assert len(pages) == 1
        assert len(pages[0]) == 1

    def test_paginate_multiple_pages(
        self, generator: RemarkableGenerator
    ) -> None:
        """Many blocks should split across multiple pages."""
        # Create blocks that will exceed one page
        blocks = []
        for i in range(100):
            blocks.append(
                ContentBlock(
                    type=BlockType.PARAGRAPH,
                    level=0,
                    text=f"Paragraph {i + 1}. " * 50,  # Long text
                    formatting=[],
                )
            )

        pages = generator.paginate_content(blocks)

        assert len(pages) > 1
        # Each page should have some blocks
        for page in pages:
            assert len(page) > 0

    def test_header_near_bottom_starts_new_page(
        self, generator: RemarkableGenerator
    ) -> None:
        """Headers near page bottom should start new page."""
        blocks = []

        # Fill almost a full page
        for i in range(40):
            blocks.append(
                ContentBlock(
                    type=BlockType.PARAGRAPH,
                    level=0,
                    text="Short paragraph",
                    formatting=[],
                )
            )

        # Add a header (should start new page)
        blocks.append(
            ContentBlock(
                type=BlockType.HEADER,
                level=1,
                text="New Section",
                formatting=[],
            )
        )

        pages = generator.paginate_content(blocks)

        # Header should be on page 2
        assert len(pages) >= 2
        # Last page should start with the header
        assert pages[-1][0].type == BlockType.HEADER

    def test_blocks_not_split(
        self, generator: RemarkableGenerator
    ) -> None:
        """Blocks should never be split across pages."""
        blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text="A" * 1000,  # Very long paragraph
                formatting=[],
            )
        ]

        pages = generator.paginate_content(blocks)

        # Block should be on exactly one page
        assert len(pages) == 1
        assert len(pages[0]) == 1


class TestEstimateBlockLines:
    """Tests for block line estimation."""

    def test_horizontal_rule(self, generator: RemarkableGenerator) -> None:
        """Horizontal rules should take 2 lines."""
        block = ContentBlock(
            type=BlockType.HORIZONTAL_RULE,
            level=0,
            text="---",
            formatting=[],
        )

        lines = generator.estimate_block_lines(block)
        assert lines == 2

    def test_short_paragraph(self, generator: RemarkableGenerator) -> None:
        """Short paragraph should take minimal lines."""
        block = ContentBlock(
            type=BlockType.PARAGRAPH,
            level=0,
            text="Short text",
            formatting=[],
        )

        lines = generator.estimate_block_lines(block)
        assert lines >= 2  # At least text + spacing

    def test_long_paragraph(self, generator: RemarkableGenerator) -> None:
        """Long paragraph should take multiple lines."""
        block = ContentBlock(
            type=BlockType.PARAGRAPH,
            level=0,
            text="A" * 2000,  # Very long text
            formatting=[],
        )

        lines = generator.estimate_block_lines(block)
        assert lines > 10  # Should wrap many times

    def test_header_extra_spacing(
        self, generator: RemarkableGenerator
    ) -> None:
        """Headers should have extra spacing."""
        para = ContentBlock(
            type=BlockType.PARAGRAPH,
            level=0,
            text="Text",
            formatting=[],
        )
        header = ContentBlock(
            type=BlockType.HEADER,
            level=1,
            text="Text",
            formatting=[],
        )

        # Header should take more lines due to spacing
        assert generator.estimate_block_lines(header) > generator.estimate_block_lines(
            para
        )

    def test_code_block_line_count(self, generator: RemarkableGenerator) -> None:
        """Code blocks should count newlines plus spacing."""
        code_text = "def hello():\n    print('world')\n    return True"
        block = ContentBlock(
            type=BlockType.CODE_BLOCK,
            level=0,
            text=code_text,
            formatting=[],
        )

        lines = generator.estimate_block_lines(block)
        # Should be number of newlines + 2 for spacing
        expected_lines = code_text.count("\n") + 2
        assert lines == expected_lines


class TestBlocksToTextItems:
    """Tests for converting blocks to positioned text items."""

    def test_single_paragraph(self, generator: RemarkableGenerator) -> None:
        """Single paragraph should create one text item."""
        blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text="Test paragraph",
                formatting=[],
            )
        ]

        items = generator.blocks_to_text_items(blocks)

        assert len(items) == 1
        item = items[0]
        assert item.text == "Test paragraph"
        assert item.x == generator.layout.margin_left
        assert item.y == generator.layout.margin_top
        assert item.width > 0

    def test_multiple_blocks(self, generator: RemarkableGenerator) -> None:
        """Multiple blocks should have increasing Y positions."""
        blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text="First",
                formatting=[],
            ),
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text="Second",
                formatting=[],
            ),
        ]

        items = generator.blocks_to_text_items(blocks)

        assert len(items) == 2
        assert items[1].y > items[0].y

    def test_list_item_indentation(
        self, generator: RemarkableGenerator
    ) -> None:
        """List items should be indented."""
        blocks = [
            ContentBlock(
                type=BlockType.LIST_ITEM,
                level=1,
                text="Item 1",
                formatting=[],
            )
        ]

        items = generator.blocks_to_text_items(blocks)

        assert len(items) == 1
        item = items[0]
        # Should be indented from left margin
        assert item.x > generator.layout.margin_left
        # Should have bullet
        assert item.text.startswith("•")

    def test_nested_list_more_indented(
        self, generator: RemarkableGenerator
    ) -> None:
        """Nested lists should have more indentation."""
        blocks = [
            ContentBlock(
                type=BlockType.LIST_ITEM,
                level=1,
                text="Level 1",
                formatting=[],
            ),
            ContentBlock(
                type=BlockType.LIST_ITEM,
                level=2,
                text="Level 2",
                formatting=[],
            ),
        ]

        items = generator.blocks_to_text_items(blocks)

        assert items[1].x > items[0].x

    def test_horizontal_rule_skipped(
        self, generator: RemarkableGenerator
    ) -> None:
        """Horizontal rules should not create text items."""
        blocks = [
            ContentBlock(
                type=BlockType.HORIZONTAL_RULE,
                level=0,
                text="---",
                formatting=[],
            )
        ]

        items = generator.blocks_to_text_items(blocks)

        # No text item, just spacing
        assert len(items) == 0

    def test_formatting_preserved(
        self, generator: RemarkableGenerator
    ) -> None:
        """Formatting information should be preserved."""
        formatting = [TextFormat(start=0, end=4, style=FormatStyle.BOLD)]
        blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text="Bold text",
                formatting=formatting,
            )
        ]

        items = generator.blocks_to_text_items(blocks)

        assert len(items) == 1
        assert items[0].formatting == formatting


class TestGenerateRmFile:
    """Tests for .rm file generation with rmscene."""

    def test_generates_valid_rm_file(
        self, generator: RemarkableGenerator
    ) -> None:
        """Generated .rm file should be valid v6 format."""
        page = RemarkablePage(
            uuid="test-page",
            text_items=[
                TextItem(
                    text="Test content",
                    x=50.0,
                    y=50.0,
                    width=1300.0,
                    formatting=[],
                )
            ],
        )

        rm_bytes = generator.generate_rm_file(page)

        # Should have content
        assert len(rm_bytes) > 0

        # Should start with v6 header
        assert rm_bytes.startswith(b"reMarkable .lines file, version=6")

    def test_generated_file_is_parseable(
        self, generator: RemarkableGenerator
    ) -> None:
        """Generated .rm file should be readable by rmscene."""
        import io

        page = RemarkablePage(
            uuid="test-page",
            text_items=[
                TextItem(
                    text="Test text",
                    x=50.0,
                    y=50.0,
                    width=1300.0,
                    formatting=[],
                )
            ],
        )

        rm_bytes = generator.generate_rm_file(page)

        # Should be parseable
        buffer = io.BytesIO(rm_bytes)
        blocks = list(rmscene.read_blocks(buffer))
        assert len(blocks) > 0

    def test_empty_page(self, generator: RemarkableGenerator) -> None:
        """Empty page should generate valid file."""
        page = RemarkablePage(uuid="empty-page", text_items=[])

        rm_bytes = generator.generate_rm_file(page)

        # Should still generate valid file
        assert len(rm_bytes) > 0

    def test_multiple_text_items(
        self, generator: RemarkableGenerator
    ) -> None:
        """Page with multiple text items should generate file."""
        page = RemarkablePage(
            uuid="multi-page",
            text_items=[
                TextItem(text="First", x=50.0, y=50.0, width=1300.0, formatting=[]),
                TextItem(text="Second", x=50.0, y=100.0, width=1300.0, formatting=[]),
                TextItem(text="Third", x=50.0, y=150.0, width=1300.0, formatting=[]),
            ],
        )

        rm_bytes = generator.generate_rm_file(page)

        assert len(rm_bytes) > 0


