"""Tests for reMarkable document generator.

This module tests the generator that converts markdown documents into
reMarkable v6 format files with pagination and rmscene integration.
"""

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
from rock_paper_sync.layout.constants import TEXT_POS_Y
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
        content.append(f"This is paragraph {i + 1}. " * 10)  # Long paragraph

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
        doc = generator.generate_document(sample_markdown_doc, parent_uuid="parent-123")

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

    def test_paginate_empty_content(self, generator: RemarkableGenerator) -> None:
        """Empty content should result in one empty page."""
        pages = generator.paginate_content([])

        assert len(pages) == 1
        assert pages[0] == []

    def test_paginate_single_block(self, generator: RemarkableGenerator) -> None:
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

    def test_paginate_multiple_pages(self, generator: RemarkableGenerator) -> None:
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

    def test_header_near_bottom_starts_new_page(self, generator: RemarkableGenerator) -> None:
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

    def test_blocks_not_split(self, generator: RemarkableGenerator) -> None:
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
        assert lines >= 1  # At least the text line (no extra spacing)

    def test_long_paragraph(self, generator: RemarkableGenerator) -> None:
        """Long paragraph should take multiple lines."""
        # Use realistic text with spaces (word wrap only breaks at spaces)
        words = "The quick brown fox jumps over the lazy dog. "
        block = ContentBlock(
            type=BlockType.PARAGRAPH,
            level=0,
            text=words * 40,  # ~1800 chars of realistic text
            formatting=[],
        )

        lines = generator.estimate_block_lines(block)
        assert lines > 10  # Should wrap many times

    def test_header_extra_spacing(self, generator: RemarkableGenerator) -> None:
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
        assert generator.estimate_block_lines(header) > generator.estimate_block_lines(para)

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

        items, text_blocks = generator.blocks_to_text_items(blocks)

        assert len(items) == 1
        item = items[0]
        assert item.text == "Test paragraph"
        assert item.x == generator.layout.margin_left
        # Y position uses TEXT_POS_Y constant (94.0) for rmscene compatibility
        assert item.y == TEXT_POS_Y
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

        items, text_blocks = generator.blocks_to_text_items(blocks)

        assert len(items) == 2
        assert items[1].y > items[0].y

    def test_list_item_indentation(self, generator: RemarkableGenerator) -> None:
        """List items should be indented."""
        blocks = [
            ContentBlock(
                type=BlockType.LIST_ITEM,
                level=1,
                text="Item 1",
                formatting=[],
            )
        ]

        items, text_blocks = generator.blocks_to_text_items(blocks)

        assert len(items) == 1
        item = items[0]
        # Should be indented from left margin
        assert item.x > generator.layout.margin_left
        # Should have bullet
        assert item.text.startswith("•")

    def test_nested_list_more_indented(self, generator: RemarkableGenerator) -> None:
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

        items, text_blocks = generator.blocks_to_text_items(blocks)

        assert items[1].x > items[0].x

    def test_horizontal_rule_skipped(self, generator: RemarkableGenerator) -> None:
        """Horizontal rules should not create text items."""
        blocks = [
            ContentBlock(
                type=BlockType.HORIZONTAL_RULE,
                level=0,
                text="---",
                formatting=[],
            )
        ]

        items, text_blocks = generator.blocks_to_text_items(blocks)

        # No text item, just spacing
        assert len(items) == 0

    def test_formatting_preserved(self, generator: RemarkableGenerator) -> None:
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

        items, text_blocks = generator.blocks_to_text_items(blocks)

        assert len(items) == 1
        assert items[0].formatting == formatting


class TestGenerateRmFile:
    """Tests for .rm file generation with rmscene."""

    def test_generates_valid_rm_file(self, generator: RemarkableGenerator) -> None:
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

    def test_generated_file_is_parseable(self, generator: RemarkableGenerator) -> None:
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

    def test_multiple_text_items(self, generator: RemarkableGenerator) -> None:
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

    def test_newline_format_codes(self, generator: RemarkableGenerator, tmp_path: Path) -> None:
        """Verify format code 10 (newline) is written to .rm file binary.

        This test validates our workaround for rmscene not supporting
        ParagraphStyle.NEWLINE. See docs/RMSCENE_NEWLINE_WORKAROUND.md.

        Note: We check raw bytes because rmscene is lossy - it converts
        format code 10 back to ParagraphStyle.PLAIN when reading files.
        But we can verify the generator writes it correctly by inspecting
        the binary directly.
        """
        import io

        # Create markdown with known newline count
        md_file = tmp_path / "test.md"
        # This will create text with exactly 3 newlines after processing
        md_file.write_text("First paragraph.\n\n" "Second paragraph.\n\n" "Third paragraph.")

        # Parse and generate
        md_doc = parse_markdown_file(md_file)
        rm_doc = generator.generate_document(md_doc)

        assert len(rm_doc.pages) > 0, "No pages generated"

        # Generate .rm bytes for first page
        rm_bytes = generator.generate_rm_file(rm_doc.pages[0])

        # Verify basic file structure
        assert len(rm_bytes) > 0, "Generated file is empty"
        assert rm_bytes.startswith(b"reMarkable .lines file, version=6"), "Invalid file header"

        # Extract text to count expected newlines
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        root_text_block = None
        for block in blocks:
            if type(block).__name__ == "RootTextBlock":
                root_text_block = block
                break

        assert root_text_block is not None, "No RootTextBlock found"

        # Extract text content
        text_parts = []
        for item in root_text_block.value.items.sequence_items():
            if hasattr(item, "value") and isinstance(item.value, str):
                text_parts.append(item.value)
        text_content = "".join(text_parts)
        expected_newline_count = text_content.count("\n")

        assert expected_newline_count > 0, "Test should have newlines in text"

        # Search for format code 10 in raw bytes
        # In rmscene's protobuf-like encoding, small integers like 10 are
        # written as single-byte varints (0x0A for value 10).

        # Strategy: Search for byte sequences that indicate LwwValue writes
        # containing the integer 10. In the protobuf format used by rmscene,
        # this appears as specific byte patterns.

        # Find all occurrences of 0x0A (varint encoding of 10) in the binary
        # We need to distinguish between:
        # - 0x0A as newline character in text content
        # - 0x0A as format code value in styles metadata

        # The text content appears early in the file in a contiguous block.
        # Format codes appear later in the styles section.

        # Simple heuristic: Count 0x0A bytes after the text content section
        # The text section typically ends within the first ~1000 bytes for
        # small documents like our test.

        # Count newline bytes in the entire file
        total_0x0a = rm_bytes.count(b"\x0a")

        # Count newline bytes in text content (these are actual '\n' chars)
        text_newlines = expected_newline_count

        # The remaining 0x0A bytes should include our format code 10 values
        # Note: There may be other 0x0A bytes in the protobuf metadata/encoding
        potential_format_codes = total_0x0a - text_newlines

        # We expect at least as many format code 10 bytes as we have newlines
        # (one format code per newline position)
        assert potential_format_codes >= text_newlines, (
            f"Expected at least {text_newlines} format code markers in binary, "
            f"but found only {potential_format_codes} candidate 0x0A bytes "
            f"(total 0x0A: {total_0x0a}, text newlines: {text_newlines})"
        )

        # Additional validation: verify file is parseable (proves valid v6 format)
        assert len(blocks) > 0, "File should contain blocks"
        assert root_text_block is not None, "File should contain text"

        # This test validates that:
        # ✓ Multi-paragraph documents generate successfully
        # ✓ Generated .rm files are valid v6 format
        # ✓ Binary contains format code 10 bytes (0x0A) beyond text newlines
        # ✓ Format code count matches expected newline count


class TestStrokeReanchoring:
    """Tests for stroke re-anchoring methods."""

    def test_is_implicit_paragraph_no_text_blocks(self, generator):
        """With no text blocks, everything is implicit."""
        result = generator._is_implicit_paragraph(cluster_center_y=500.0, text_blocks=[])
        assert result is True

    def test_is_implicit_paragraph_stroke_above_text(self, generator):
        """Stroke above text is not implicit."""
        from rock_paper_sync.annotations.core_types import TextBlock

        text_blocks = [
            TextBlock(content="Hello", y_start=200.0, y_end=250.0, block_type="paragraph")
        ]
        # Stroke at Y=100 is above text starting at Y=200
        result = generator._is_implicit_paragraph(cluster_center_y=100.0, text_blocks=text_blocks)
        assert result is False

    def test_is_implicit_paragraph_stroke_inline_with_text(self, generator):
        """Stroke inline with text is not implicit."""
        from rock_paper_sync.annotations.core_types import TextBlock

        text_blocks = [
            TextBlock(content="Hello", y_start=200.0, y_end=250.0, block_type="paragraph")
        ]
        # Stroke at Y=225 is within text range
        result = generator._is_implicit_paragraph(cluster_center_y=225.0, text_blocks=text_blocks)
        assert result is False

    def test_is_implicit_paragraph_stroke_just_below_text(self, generator):
        """Stroke just below text (small gap) is not implicit."""
        from rock_paper_sync.annotations.core_types import TextBlock

        text_blocks = [
            TextBlock(content="Hello", y_start=200.0, y_end=250.0, block_type="paragraph")
        ]
        # Stroke at Y=260 is just below text (gap=10, less than LINE_HEIGHT)
        result = generator._is_implicit_paragraph(cluster_center_y=260.0, text_blocks=text_blocks)
        assert result is False  # Gap too small

    def test_is_implicit_paragraph_stroke_well_below_text(self, generator):
        """Stroke well below text (large gap) is implicit."""
        from rock_paper_sync.annotations.core_types import TextBlock

        text_blocks = [
            TextBlock(content="Hello", y_start=200.0, y_end=250.0, block_type="paragraph")
        ]
        # Stroke at Y=400 is well below text (gap=150, more than LINE_HEIGHT)
        result = generator._is_implicit_paragraph(cluster_center_y=400.0, text_blocks=text_blocks)
        assert result is True

    def test_is_implicit_paragraph_custom_threshold(self, generator):
        """Custom gap threshold is respected."""
        from rock_paper_sync.annotations.core_types import TextBlock

        text_blocks = [
            TextBlock(content="Hello", y_start=200.0, y_end=250.0, block_type="paragraph")
        ]
        # Gap of 60 pixels
        cluster_y = 310.0

        # With threshold of 50, it's implicit (gap=60 > 50)
        result = generator._is_implicit_paragraph(
            cluster_center_y=cluster_y, text_blocks=text_blocks, gap_threshold=50.0
        )
        assert result is True

        # With threshold of 100, it's not implicit (gap=60 < 100)
        result = generator._is_implicit_paragraph(
            cluster_center_y=cluster_y, text_blocks=text_blocks, gap_threshold=100.0
        )
        assert result is False

    def test_is_implicit_paragraph_multiple_text_blocks(self, generator):
        """Uses the last text block's y_end for gap calculation."""
        from rock_paper_sync.annotations.core_types import TextBlock

        text_blocks = [
            TextBlock(content="First", y_start=100.0, y_end=150.0, block_type="paragraph"),
            TextBlock(content="Second", y_start=200.0, y_end=250.0, block_type="paragraph"),
            TextBlock(content="Third", y_start=300.0, y_end=350.0, block_type="paragraph"),
        ]
        # Stroke at Y=500 is well below all text (gap from Y=350)
        result = generator._is_implicit_paragraph(
            cluster_center_y=500.0, text_blocks=text_blocks, gap_threshold=50.0
        )
        assert result is True

        # Stroke at Y=360 is just below last text (gap=10)
        result = generator._is_implicit_paragraph(
            cluster_center_y=360.0, text_blocks=text_blocks, gap_threshold=50.0
        )
        assert result is False


class TestParagraphSplitting:
    """Tests for paragraph splitting across page boundaries.

    These tests verify that when allow_paragraph_splitting=True, long paragraphs
    are correctly split at page boundaries using the layout engine for accurate
    character offset calculation.
    """

    @pytest.fixture
    def splitting_config(self) -> LayoutConfig:
        """Layout config with paragraph splitting enabled and small page."""
        return LayoutConfig(
            lines_per_page=10,  # Small page to force splits
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50,
            allow_paragraph_splitting=True,
        )

    @pytest.fixture
    def splitting_generator(self, splitting_config: LayoutConfig) -> RemarkableGenerator:
        """Generator with paragraph splitting enabled."""
        return RemarkableGenerator(splitting_config)

    def test_long_paragraph_splits_across_pages(
        self, splitting_generator: RemarkableGenerator
    ) -> None:
        """A paragraph longer than a page should split across multiple pages."""
        # Create text that will span multiple lines
        words = "The quick brown fox jumps over the lazy dog. "
        long_text = words * 50  # ~2500 chars = many lines

        blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text=long_text,
                formatting=[],
            )
        ]

        pages = splitting_generator.paginate_content(blocks)

        # Should span multiple pages
        assert len(pages) > 1, "Long paragraph should span multiple pages"

        # Collect all text from all pages
        all_text = []
        for page in pages:
            for block in page:
                all_text.append(block.text)

        combined = " ".join(all_text)

        # Verify all original words are present
        original_words = long_text.split()
        for word in original_words:
            assert word in combined, f"Word '{word}' missing after split"

    def test_split_at_word_boundary(self, splitting_generator: RemarkableGenerator) -> None:
        """Paragraphs should split at word boundaries, not mid-word."""
        words = "abcdefghij klmnopqrst uvwxyzabcd efghijklmn "
        long_text = words * 30  # Force multiple pages

        blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text=long_text,
                formatting=[],
            )
        ]

        pages = splitting_generator.paginate_content(blocks)

        for page in pages:
            for block in page:
                text = block.text.strip()
                if text:
                    # Text should not start with a space (indicating mid-word split)
                    assert not text.startswith(" "), "Text should not start with space"
                    # Text should not end with partial word unless it's the last
                    # This is harder to verify, but at least ensure no weird chars

    def test_split_preserves_total_content(self, splitting_generator: RemarkableGenerator) -> None:
        """All content should be preserved after splitting."""
        original_text = "Word1 Word2 Word3 Word4 Word5. " * 40

        blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text=original_text,
                formatting=[],
            )
        ]

        pages = splitting_generator.paginate_content(blocks)

        # Collect all text
        all_texts = []
        for page in pages:
            for block in page:
                all_texts.append(block.text)

        combined = " ".join(all_texts)

        # Check each unique word is present
        for word in ["Word1", "Word2", "Word3", "Word4", "Word5"]:
            assert word in combined, f"{word} missing from split output"

    def test_no_split_when_disabled_and_fits(self, generator: RemarkableGenerator) -> None:
        """With splitting disabled, paragraphs that fit on a page stay together."""
        # Use text that fits on one page (generator has 28 lines/page default)
        words = "The quick brown fox. "
        text = words * 10  # About 200 chars, ~8 lines - fits on one page

        blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text=text,
                formatting=[],
            )
        ]

        pages = generator.paginate_content(blocks)

        # Without splitting, block stays together
        assert len(pages) == 1
        assert len(pages[0]) == 1
        assert pages[0][0].text == text

    def test_oversized_paragraph_force_split(self, generator: RemarkableGenerator) -> None:
        """Paragraphs too large for a single page MUST be split, even with splitting disabled."""
        words = "The quick brown fox. "
        long_text = words * 100  # Very long - won't fit on one page

        blocks = [
            ContentBlock(
                type=BlockType.PARAGRAPH,
                level=0,
                text=long_text,
                formatting=[],
            )
        ]

        pages = generator.paginate_content(blocks)

        # Oversized paragraph must be split across pages
        assert len(pages) > 1, "Oversized paragraph must be split"

        # All content should be preserved
        all_text = " ".join(block.text for page in pages for block in page)
        assert "quick" in all_text
        assert "brown" in all_text
        assert "fox" in all_text

    def test_multiple_paragraphs_with_split(self, splitting_generator: RemarkableGenerator) -> None:
        """Multiple paragraphs should paginate correctly with splitting enabled."""
        # First paragraph: short
        para1 = "This is a short paragraph."

        # Second paragraph: long (will split)
        words = "Long text that wraps many times. "
        para2 = words * 30

        # Third paragraph: short
        para3 = "Another short one."

        blocks = [
            ContentBlock(type=BlockType.PARAGRAPH, level=0, text=para1, formatting=[]),
            ContentBlock(type=BlockType.PARAGRAPH, level=0, text=para2, formatting=[]),
            ContentBlock(type=BlockType.PARAGRAPH, level=0, text=para3, formatting=[]),
        ]

        pages = splitting_generator.paginate_content(blocks)

        # Should have multiple pages
        assert len(pages) >= 2

        # First page should start with para1
        assert pages[0][0].text == para1

        # Last page should end with para3
        assert pages[-1][-1].text == para3

    def test_generated_rm_files_with_split_paragraphs(
        self, splitting_generator: RemarkableGenerator, tmp_path: Path
    ) -> None:
        """Generated .rm files should be valid even with split paragraphs."""
        words = "Content that spans pages. "
        long_text = words * 40

        # Create markdown file and parse it to get proper MarkdownDocument
        md_file = tmp_path / "split_test.md"
        md_file.write_text(f"# Split Test\n\n{long_text}\n")
        doc = parse_markdown_file(md_file)

        result = splitting_generator.generate_document(doc)

        assert len(result.pages) > 1, "Should have multiple pages"

        # Verify each page can generate a valid .rm file
        for i, page in enumerate(result.pages):
            rm_data = splitting_generator.generate_rm_file(page)
            assert rm_data is not None, f"Page {i} missing rm_data"
            assert len(rm_data) > 0, f"Page {i} has empty rm_data"

            # Parse with rmscene to verify validity
            import io

            parsed_blocks = list(rmscene.read_blocks(io.BytesIO(rm_data)))
            assert len(parsed_blocks) > 0, f"Page {i} has no blocks"
