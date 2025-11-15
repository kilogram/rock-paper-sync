"""Comprehensive round-trip integration tests with rmscene.

These tests verify that markdown → reMarkable → read back with rmscene
preserves the essential content and structure.
"""

import io
from pathlib import Path

import pytest
import rmscene

from rm_obsidian_sync.config import LayoutConfig
from rm_obsidian_sync.generator import RemarkableGenerator
from rm_obsidian_sync.parser import (
    BlockType,
    ContentBlock,
    FormatStyle,
    MarkdownDocument,
    TextFormat,
    parse_markdown_file,
)


class TestRoundtripBasic:
    """Basic round-trip tests for simple documents."""

    def test_simple_text_document_roundtrip(self, tmp_path: Path) -> None:
        """Test simple_text_document from rmscene can be read back."""
        # Create simple text using rmscene
        text = "This is a simple test document.\nWith multiple lines.\nAnd content."

        blocks = list(rmscene.simple_text_document(text))

        # Write to .rm file
        rm_file = tmp_path / "test.rm"
        with rm_file.open('wb') as f:
            rmscene.write_blocks(f, blocks)

        # Read back
        with rm_file.open('rb') as f:
            read_blocks = list(rmscene.read_blocks(f))

        # Verify we got blocks back
        assert len(read_blocks) > 0

        # Extract text from blocks
        extracted_text = extract_text_from_blocks(read_blocks)

        # Verify content preserved
        assert "simple test document" in extracted_text
        assert "multiple lines" in extracted_text
        assert "content" in extracted_text

    def test_markdown_to_rm_simple_roundtrip(self, tmp_path: Path) -> None:
        """Test markdown → RM → parse back → verify text matches."""
        # Create markdown file
        md_file = tmp_path / "test.md"
        md_content = """# Test Document

This is **bold** text and this is *italic* text.

## Section 2

Another paragraph here.
"""
        md_file.write_text(md_content)

        # Parse markdown
        md_doc = parse_markdown_file(md_file)

        # Generate .rm file using our generator
        layout = LayoutConfig(
            lines_per_page=45,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        )
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Write document files
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        generator.write_document_files(rm_doc, output_dir)

        # Read back .rm file with rmscene
        rm_file = output_dir / rm_doc.uuid / f"{rm_doc.pages[0].uuid}.rm"
        assert rm_file.exists()

        with rm_file.open('rb') as f:
            blocks = list(rmscene.read_blocks(f))

        # Extract text from blocks
        extracted_text = extract_text_from_blocks(blocks)

        # Verify key content is present
        assert "Test Document" in extracted_text
        assert "bold" in extracted_text
        assert "italic" in extracted_text
        assert "Section 2" in extracted_text
        assert "Another paragraph" in extracted_text


class TestRoundtripMultiPage:
    """Round-trip tests for multi-page documents."""

    def test_multi_page_document_roundtrip(self, tmp_path: Path) -> None:
        """Test multi-page document round-trip preserves all pages."""
        # Create long markdown file
        md_file = tmp_path / "long.md"
        sections = []
        for i in range(10):
            sections.append(f"## Section {i+1}\n\n" +
                          "Lorem ipsum dolor sit amet. " * 20)
        md_file.write_text("\n\n".join(sections))

        # Parse and generate
        md_doc = parse_markdown_file(md_file)
        layout = LayoutConfig(
            lines_per_page=10,  # Force multiple pages
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        )
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Should have multiple pages
        assert len(rm_doc.pages) > 1

        # Write files
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        generator.write_document_files(rm_doc, output_dir)

        # Read back each page and verify content
        all_text = []
        for page in rm_doc.pages:
            rm_file = output_dir / rm_doc.uuid / f"{page.uuid}.rm"
            assert rm_file.exists()

            with rm_file.open('rb') as f:
                blocks = list(rmscene.read_blocks(f))

            page_text = extract_text_from_blocks(blocks)
            all_text.append(page_text)

        # Combine all pages
        combined_text = " ".join(all_text)

        # Verify sections are present
        for i in range(10):
            assert f"Section {i+1}" in combined_text

    def test_empty_page_roundtrip(self, tmp_path: Path) -> None:
        """Test empty page can be written and read back."""
        # Create markdown with minimal content
        md_file = tmp_path / "empty.md"
        md_file.write_text("")

        # Parse and generate
        md_doc = parse_markdown_file(md_file)
        layout = LayoutConfig(
            lines_per_page=45,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        )
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Write files
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        generator.write_document_files(rm_doc, output_dir)

        # Read back
        rm_file = output_dir / rm_doc.uuid / f"{rm_doc.pages[0].uuid}.rm"
        with rm_file.open('rb') as f:
            blocks = list(rmscene.read_blocks(f))

        # Should have at least minimal structure
        assert len(blocks) > 0


class TestRoundtripFormatting:
    """Round-trip tests for formatted content."""

    def test_formatting_preservation_roundtrip(self, tmp_path: Path) -> None:
        """Test that formatting survives conversion (even if not visually rendered)."""
        # Create markdown with various formatting
        md_file = tmp_path / "formatted.md"
        md_content = """# Formatted Document

This has **bold text** in it.

This has *italic text* in it.

This has `code spans` in it.

This has [links](https://example.com) in it.
"""
        md_file.write_text(md_content)

        # Parse markdown - verify formatting is captured
        md_doc = parse_markdown_file(md_file)

        # Find blocks with formatting
        bold_block = None
        italic_block = None
        code_block = None
        link_block = None

        for block in md_doc.content:
            if "bold text" in block.text:
                bold_block = block
            if "italic text" in block.text:
                italic_block = block
            if "code spans" in block.text:
                code_block = block
            if "links" in block.text:
                link_block = block

        # Verify formatting was parsed
        assert bold_block is not None
        assert any(fmt.style == FormatStyle.BOLD for fmt in bold_block.formatting)

        assert italic_block is not None
        assert any(fmt.style == FormatStyle.ITALIC for fmt in italic_block.formatting)

        assert code_block is not None
        assert any(fmt.style == FormatStyle.CODE for fmt in code_block.formatting)

        assert link_block is not None
        assert any(fmt.style == FormatStyle.LINK for fmt in link_block.formatting)

        # Generate .rm file
        layout = LayoutConfig(
            lines_per_page=45,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        )
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Write files
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        generator.write_document_files(rm_doc, output_dir)

        # Read back
        rm_file = output_dir / rm_doc.uuid / f"{rm_doc.pages[0].uuid}.rm"
        with rm_file.open('rb') as f:
            blocks = list(rmscene.read_blocks(f))

        # Extract text
        extracted_text = extract_text_from_blocks(blocks)

        # Verify text content is preserved (formatting metadata may not be)
        assert "bold text" in extracted_text
        assert "italic text" in extracted_text
        assert "code spans" in extracted_text
        assert "links" in extracted_text


class TestRoundtripComplex:
    """Round-trip tests for complex content."""

    def test_complex_content_roundtrip(self, tmp_path: Path) -> None:
        """Test complex markdown with headers, lists, code blocks, etc."""
        md_file = tmp_path / "complex.md"
        md_content = """---
title: Complex Document
author: Test Author
---

# Main Heading

This is an introduction paragraph.

## Features

- Feature one with **bold**
- Feature two with *italic*
- Feature three

### Code Example

```python
def hello():
    print("Hello, World!")
```

## Lists

1. First item
2. Second item
3. Third item

### Nested Lists

- Level 1
  - Level 2
    - Level 3

> This is a blockquote with important information.

---

## Conclusion

That's all folks!
"""
        md_file.write_text(md_content)

        # Parse markdown
        md_doc = parse_markdown_file(md_file)

        # Verify frontmatter
        assert md_doc.frontmatter["title"] == "Complex Document"
        assert md_doc.frontmatter["author"] == "Test Author"

        # Verify different block types are present
        block_types = {block.type for block in md_doc.content}
        assert BlockType.HEADER in block_types
        assert BlockType.PARAGRAPH in block_types
        assert BlockType.LIST_ITEM in block_types
        assert BlockType.CODE_BLOCK in block_types
        assert BlockType.BLOCKQUOTE in block_types
        assert BlockType.HORIZONTAL_RULE in block_types

        # Generate .rm file
        layout = LayoutConfig(
            lines_per_page=45,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        )
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Write files
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        generator.write_document_files(rm_doc, output_dir)

        # Read back and verify structure exists
        for page in rm_doc.pages:
            rm_file = output_dir / rm_doc.uuid / f"{page.uuid}.rm"
            assert rm_file.exists()

            with rm_file.open('rb') as f:
                blocks = list(rmscene.read_blocks(f))

            # Verify blocks exist
            assert len(blocks) > 0

    def test_unicode_content_roundtrip(self, tmp_path: Path) -> None:
        """Test Unicode content survives round-trip."""
        md_file = tmp_path / "unicode.md"
        md_content = """# Unicode Test

This has émojis: 🎉 🚀 ✨

And special characters: café, naïve, résumé

And symbols: ™ © ® € £ ¥

And math: ∑ ∫ ∂ √ ∞
"""
        md_file.write_text(md_content)

        # Parse and generate
        md_doc = parse_markdown_file(md_file)
        layout = LayoutConfig(
            lines_per_page=45,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        )
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Write files
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        generator.write_document_files(rm_doc, output_dir)

        # Read back
        rm_file = output_dir / rm_doc.uuid / f"{rm_doc.pages[0].uuid}.rm"
        with rm_file.open('rb') as f:
            blocks = list(rmscene.read_blocks(f))

        # Extract text
        extracted_text = extract_text_from_blocks(blocks)

        # Verify Unicode preserved (at least the basic characters)
        assert "café" in extracted_text or "cafe" in extracted_text
        assert "résumé" in extracted_text or "resume" in extracted_text


class TestRoundtripMetadata:
    """Round-trip tests for metadata preservation."""

    def test_metadata_files_valid_json(self, tmp_path: Path) -> None:
        """Test that generated metadata files are valid JSON."""
        import json

        # Create simple markdown
        md_file = tmp_path / "test.md"
        md_file.write_text("# Test\n\nContent here.")

        # Parse and generate
        md_doc = parse_markdown_file(md_file)
        layout = LayoutConfig(
            lines_per_page=45,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        )
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Write files
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        generator.write_document_files(rm_doc, output_dir)

        # Verify metadata files are valid JSON
        doc_dir = output_dir / rm_doc.uuid

        # Document metadata
        metadata_file = doc_dir / f"{rm_doc.uuid}.metadata"
        with metadata_file.open() as f:
            metadata = json.load(f)
        assert metadata["visibleName"] == "test"
        assert metadata["type"] == "DocumentType"

        # Content file
        content_file = doc_dir / f"{rm_doc.uuid}.content"
        with content_file.open() as f:
            content = json.load(f)
        assert "pages" in content
        assert len(content["pages"]) == len(rm_doc.pages)

        # Page metadata
        for page in rm_doc.pages:
            page_meta_file = doc_dir / f"{page.uuid}-metadata.json"
            with page_meta_file.open() as f:
                page_meta = json.load(f)
            assert "layers" in page_meta

    def test_document_title_preserved(self, tmp_path: Path) -> None:
        """Test that document title from frontmatter is preserved."""
        md_file = tmp_path / "titled.md"
        md_content = """---
title: My Awesome Document
---

# Content Here
"""
        md_file.write_text(md_content)

        # Parse and generate
        md_doc = parse_markdown_file(md_file)
        assert md_doc.title == "My Awesome Document"

        layout = LayoutConfig(
            lines_per_page=45,
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50
        )
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Verify title carried through
        assert rm_doc.visible_name == "My Awesome Document"


# Helper functions

def extract_text_from_blocks(blocks: list) -> str:
    """Extract text content from rmscene blocks.

    This is a helper function to parse rmscene block structures
    and extract the actual text content.
    """
    from rmscene.scene_stream import RootTextBlock

    text_parts = []

    for block in blocks:
        # Look for RootTextBlock which contains the text
        if isinstance(block, RootTextBlock):
            # RootTextBlock has a value attribute which is a Text object
            # Text has an items attribute which is a CrdtSequence
            if hasattr(block, 'value') and hasattr(block.value, 'items'):
                # CrdtSequence has an internal _items dict mapping CrdtId -> CrdtSequenceItem
                if hasattr(block.value.items, '_items'):
                    for item_id, item_data in block.value.items._items.items():
                        # Each CrdtSequenceItem has a value which is the actual text string
                        if hasattr(item_data, 'value') and isinstance(item_data.value, str):
                            text_parts.append(item_data.value)

    return " ".join(text_parts)
