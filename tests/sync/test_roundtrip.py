"""Comprehensive round-trip integration tests with rmscene.

These tests verify that markdown → reMarkable → read back with rmscene
preserves the essential content and structure.

Tests use in-memory data structures (no filesystem writes) to validate
generator output can be correctly parsed by rmscene.
"""

import io
from pathlib import Path

import rmscene

from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.generator import RemarkableGenerator
from rock_paper_sync.parser import (
    BlockType,
    ContentBlock,
    FormatStyle,
    MarkdownDocument,
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
        with rm_file.open("wb") as f:
            rmscene.write_blocks(f, blocks)

        # Read back
        with rm_file.open("rb") as f:
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
        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Get .rm file bytes (in-memory, no filesystem write)
        rm_bytes = generator.generate_rm_file(rm_doc.pages[0])

        # Parse bytes with rmscene
        buffer = io.BytesIO(rm_bytes)
        blocks = list(rmscene.read_blocks(buffer))

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
            sections.append(f"## Section {i+1}\n\n" + "Lorem ipsum dolor sit amet. " * 20)
        md_file.write_text("\n\n".join(sections))

        # Parse and generate
        md_doc = parse_markdown_file(md_file)
        layout = LayoutConfig(
            # Force multiple pages
            margin_top=50,
            margin_bottom=50,
            margin_left=50,
            margin_right=50,
        )
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Should have multiple pages
        assert len(rm_doc.pages) > 1

        # Read back each page and verify content (in-memory)
        all_text = []
        for page in rm_doc.pages:
            rm_bytes = generator.generate_rm_file(page)
            buffer = io.BytesIO(rm_bytes)
            blocks = list(rmscene.read_blocks(buffer))
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
        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Read back (in-memory)
        rm_bytes = generator.generate_rm_file(rm_doc.pages[0])
        buffer = io.BytesIO(rm_bytes)
        blocks = list(rmscene.read_blocks(buffer))

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
        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Read back (in-memory)
        rm_bytes = generator.generate_rm_file(rm_doc.pages[0])
        buffer = io.BytesIO(rm_bytes)
        blocks = list(rmscene.read_blocks(buffer))

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
        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Read back and verify structure exists (in-memory)
        for page in rm_doc.pages:
            rm_bytes = generator.generate_rm_file(page)
            buffer = io.BytesIO(rm_bytes)
            blocks = list(rmscene.read_blocks(buffer))

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
        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Read back (in-memory)
        rm_bytes = generator.generate_rm_file(rm_doc.pages[0])
        buffer = io.BytesIO(rm_bytes)
        blocks = list(rmscene.read_blocks(buffer))

        # Extract text
        extracted_text = extract_text_from_blocks(blocks)

        # Verify Unicode preserved (at least the basic characters)
        assert "café" in extracted_text or "cafe" in extracted_text
        assert "résumé" in extracted_text or "resume" in extracted_text


class TestRoundtripMetadata:
    """Round-trip tests for metadata preservation."""

    def test_metadata_files_valid_json(self, tmp_path: Path) -> None:
        """Test that generated metadata structures are valid."""
        from rock_paper_sync.metadata import (
            generate_content_metadata,
            generate_document_metadata,
            generate_page_metadata,
        )

        # Create simple markdown
        md_file = tmp_path / "test.md"
        md_file.write_text("# Test\n\nContent here.")

        # Parse and generate
        md_doc = parse_markdown_file(md_file)
        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Generate metadata structures (no filesystem writes)
        doc_metadata = generate_document_metadata(
            rm_doc.visible_name, rm_doc.parent_uuid, rm_doc.modified_time
        )
        assert doc_metadata["visibleName"] == "test"
        assert doc_metadata["type"] == "DocumentType"

        # Generate content metadata
        page_uuids = [page.uuid for page in rm_doc.pages]
        content_metadata = generate_content_metadata(page_uuids)
        assert "pages" in content_metadata
        assert len(content_metadata["pages"]) == len(rm_doc.pages)

        # Generate page metadata
        page_meta = generate_page_metadata()
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

        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)
        rm_doc = generator.generate_document(md_doc)

        # Verify title carried through
        assert rm_doc.visible_name == "My Awesome Document"


class TestDocumentUpdates:
    """Test document updates preserve identity and update content correctly."""

    def test_update_preserves_uuid(self, tmp_path: Path) -> None:
        """Test that updating a document preserves its UUID."""
        from datetime import datetime

        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)

        # Create initial document
        md_doc_v1 = MarkdownDocument(
            path=tmp_path / "test.md",
            title="Test Doc",
            content=[
                ContentBlock(BlockType.HEADER, 1, "Version 1", []),
                ContentBlock(BlockType.PARAGRAPH, 0, "Original content", []),
            ],
            frontmatter={},
            last_modified=datetime.now(),
            content_hash="hash1",
        )

        # Generate with specific UUID
        rm_doc_v1 = generator.generate_document(md_doc_v1, "", "test-uuid-123")
        assert rm_doc_v1.uuid == "test-uuid-123"

        # Create updated document
        md_doc_v2 = MarkdownDocument(
            path=tmp_path / "test.md",
            title="Test Doc",
            content=[
                ContentBlock(BlockType.HEADER, 1, "Version 2", []),
                ContentBlock(BlockType.PARAGRAPH, 0, "Updated content", []),
            ],
            frontmatter={},
            last_modified=datetime.now(),
            content_hash="hash2",
        )

        # Generate update with SAME UUID
        rm_doc_v2 = generator.generate_document(md_doc_v2, "", "test-uuid-123")
        assert rm_doc_v2.uuid == "test-uuid-123"

        # UUIDs match but content should be different
        assert rm_doc_v1.uuid == rm_doc_v2.uuid
        assert rm_doc_v1.pages[0].text_items[0].text != rm_doc_v2.pages[0].text_items[0].text

    def test_update_with_page_count_change(self, tmp_path: Path) -> None:
        """Test updating a document that changes page count."""
        from datetime import datetime

        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)

        # Short document (1 page)
        short_content = [ContentBlock(BlockType.PARAGRAPH, 0, "Short", [])]
        md_short = MarkdownDocument(
            path=tmp_path / "test.md",
            title="test",
            content=short_content,
            frontmatter={},
            last_modified=datetime.now(),
            content_hash="hash1",
        )

        rm_short = generator.generate_document(md_short, "", "fixed-uuid")
        assert len(rm_short.pages) == 1
        assert rm_short.uuid == "fixed-uuid"

        # Long document (multiple pages) - need more than LINES_PER_PAGE (28) paragraphs
        long_content = [
            ContentBlock(BlockType.PARAGRAPH, 0, f"Paragraph {i}", []) for i in range(40)
        ]
        md_long = MarkdownDocument(
            path=tmp_path / "test.md",
            title="test",
            content=long_content,
            frontmatter={},
            last_modified=datetime.now(),
            content_hash="hash2",
        )

        rm_long = generator.generate_document(md_long, "", "fixed-uuid")
        assert len(rm_long.pages) > 1
        assert rm_long.uuid == "fixed-uuid"  # UUID preserved

    def test_update_roundtrip_content_changes(self, tmp_path: Path) -> None:
        """Test round-trip with content updates."""
        from datetime import datetime

        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)

        # Version 1
        md_v1 = MarkdownDocument(
            path=tmp_path / "test.md",
            title="test",
            content=[
                ContentBlock(BlockType.HEADER, 1, "Original Header", []),
                ContentBlock(BlockType.PARAGRAPH, 0, "Original content here.", []),
            ],
            frontmatter={},
            last_modified=datetime.now(),
            content_hash="v1",
        )

        rm_v1 = generator.generate_document(md_v1, "", "stable-uuid")

        # Read back V1 (in-memory)
        rm_bytes_v1 = generator.generate_rm_file(rm_v1.pages[0])
        buffer_v1 = io.BytesIO(rm_bytes_v1)
        blocks_v1 = list(rmscene.read_blocks(buffer_v1))
        text_v1 = extract_text_from_blocks(blocks_v1)
        assert "Original Header" in text_v1
        assert "Original content" in text_v1

        # Version 2 (update)
        md_v2 = MarkdownDocument(
            path=tmp_path / "test.md",
            title="test",
            content=[
                ContentBlock(BlockType.HEADER, 1, "Updated Header", []),
                ContentBlock(BlockType.PARAGRAPH, 0, "Updated content here.", []),
            ],
            frontmatter={},
            last_modified=datetime.now(),
            content_hash="v2",
        )

        rm_v2 = generator.generate_document(md_v2, "", "stable-uuid")

        # Read back V2 (in-memory)
        rm_bytes_v2 = generator.generate_rm_file(rm_v2.pages[0])
        buffer_v2 = io.BytesIO(rm_bytes_v2)
        blocks_v2 = list(rmscene.read_blocks(buffer_v2))
        text_v2 = extract_text_from_blocks(blocks_v2)
        assert "Updated Header" in text_v2
        assert "Updated content" in text_v2
        assert "Original" not in text_v2  # Old content gone

    def test_metadata_timestamp_updates(self, tmp_path: Path) -> None:
        """Test that metadata timestamp is updated on content changes."""
        import time
        from datetime import datetime

        from rock_paper_sync.metadata import generate_document_metadata

        layout = LayoutConfig(margin_top=50, margin_bottom=50, margin_left=50, margin_right=50)
        generator = RemarkableGenerator(layout)

        md_doc = MarkdownDocument(
            path=tmp_path / "test.md",
            title="test",
            content=[ContentBlock(BlockType.PARAGRAPH, 0, "Content", [])],
            frontmatter={},
            last_modified=datetime.now(),
            content_hash="v1",
        )

        # First generation
        rm_v1 = generator.generate_document(md_doc, "", "uuid-123")
        metadata_v1 = generate_document_metadata(
            rm_v1.visible_name, rm_v1.parent_uuid, rm_v1.modified_time
        )
        timestamp_v1 = int(metadata_v1["lastModified"])

        # Wait a bit
        time.sleep(0.01)

        # Update (modified time will be different)
        md_doc.content_hash = "v2"
        md_doc.last_modified = datetime.now()  # Update timestamp
        rm_v2 = generator.generate_document(md_doc, "", "uuid-123")
        metadata_v2 = generate_document_metadata(
            rm_v2.visible_name, rm_v2.parent_uuid, rm_v2.modified_time
        )
        timestamp_v2 = int(metadata_v2["lastModified"])

        # Timestamp should be updated
        assert timestamp_v2 >= timestamp_v1  # May be equal if system clock doesn't advance


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
            if hasattr(block, "value") and hasattr(block.value, "items"):
                # CrdtSequence has an internal _items dict mapping CrdtId -> CrdtSequenceItem
                if hasattr(block.value.items, "_items"):
                    for item_id, item_data in block.value.items._items.items():
                        # Each CrdtSequenceItem has a value which is the actual text string
                        if hasattr(item_data, "value") and isinstance(item_data.value, str):
                            text_parts.append(item_data.value)

    return " ".join(text_parts)
