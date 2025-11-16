"""Unit tests for aligned annotation markers.

Tests that markers are added at correct paragraph boundaries using
ContentBlock list from parser, not naive newline splitting.
"""

import pytest
from pathlib import Path
from tempfile import NamedTemporaryFile

from rock_paper_sync.annotation_mapper import AnnotationInfo
from rock_paper_sync.annotation_markers_v2 import (
    add_annotation_markers_aligned,
    strip_annotation_markers,
    has_annotation_markers,
    count_annotation_markers,
    format_marker,
)
from rock_paper_sync.parser import parse_markdown_file, ContentBlock, BlockType


class TestParagraphAlignment:
    """Test that markers align with parser block boundaries."""

    def test_markers_align_with_parser_blocks(self):
        """Verify markers appear at correct block boundaries."""
        markdown = """# Header

Paragraph 1

- List item 1
- List item 2

Paragraph 2
"""

        with NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(markdown)
            f_path = Path(f.name)

        try:
            doc = parse_markdown_file(f_path)

            # doc.content should be:
            # [0] Header
            # [1] Paragraph ("Paragraph 1")
            # [2] ListItem ("List item 1")
            # [3] ListItem ("List item 2")
            # [4] Paragraph ("Paragraph 2")

            # Annotate block 1 (Paragraph 1)
            annotation_map = {1: AnnotationInfo(highlights=2)}

            # Add markers
            marked = add_annotation_markers_aligned(doc.content, annotation_map)

            # Verify marker appears around "Paragraph 1", not list items
            assert "<!-- ANNOTATED" in marked
            assert "Paragraph 1" in marked

            # Parse marked text to verify structure
            with NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f2:
                f2.write(marked)
                f2_path = Path(f2.name)

            try:
                # Verify marker appears before "Paragraph 1"
                lines = marked.split('\n')
                marker_line = next(i for i, line in enumerate(lines) if 'ANNOTATED' in line)
                para1_line = next(i for i, line in enumerate(lines) if 'Paragraph 1' in line)

                assert marker_line < para1_line, "Marker should appear before paragraph"

                # Verify marker does NOT appear around list items
                list_section = '\n'.join([
                    line for line in lines
                    if 'List item' in line or
                    (lines.index(line) > 0 and 'List item' in lines[lines.index(line) - 1])
                ])
                assert 'ANNOTATED' not in list_section, "Marker should not be around list items"

            finally:
                f2_path.unlink()

        finally:
            f_path.unlink()

    def test_multiple_annotated_blocks(self):
        """Test marking multiple non-consecutive blocks."""
        markdown = """Paragraph 1

Paragraph 2

Paragraph 3

Paragraph 4
"""

        with NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(markdown)
            f_path = Path(f.name)

        try:
            doc = parse_markdown_file(f_path)

            # Annotate blocks 0 and 2 (Paragraph 1 and 3)
            annotation_map = {
                0: AnnotationInfo(highlights=1),
                2: AnnotationInfo(highlights=2, strokes=1)
            }

            marked = add_annotation_markers_aligned(doc.content, annotation_map)

            # Should have 2 pairs of markers (4 total marker lines)
            assert marked.count('<!-- ANNOTATED') == 2
            assert marked.count('<!-- /ANNOTATED') == 2

            # Verify both paragraphs are marked
            assert 'Paragraph 1' in marked
            assert 'Paragraph 3' in marked

        finally:
            f_path.unlink()

    def test_complex_document_structure(self):
        """Test with complex structure (headers, lists, code)."""
        markdown = """# Main Header

Introduction paragraph.

## Subheader

Another paragraph with **bold** text.

```python
code_block = True
```

- List item 1
- List item 2

Final paragraph.
"""

        with NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(markdown)
            f_path = Path(f.name)

        try:
            doc = parse_markdown_file(f_path)

            # Find index of "Another paragraph with bold text"
            para_idx = None
            for i, block in enumerate(doc.content):
                if block.type == BlockType.PARAGRAPH and 'bold' in block.text:
                    para_idx = i
                    break

            assert para_idx is not None, "Should find the paragraph"

            # Annotate that paragraph
            annotation_map = {para_idx: AnnotationInfo(highlights=1)}

            marked = add_annotation_markers_aligned(doc.content, annotation_map)

            # Verify marker appears around correct paragraph
            assert 'ANNOTATED' in marked
            assert 'bold' in marked  # The marked paragraph

            # Verify markers don't appear around code or lists
            lines = marked.split('\n')
            for i, line in enumerate(lines):
                if 'code_block' in line or 'List item' in line:
                    # Check surrounding lines don't have markers
                    if i > 0:
                        assert 'ANNOTATED' not in lines[i-1]
                    if i < len(lines) - 1:
                        assert 'ANNOTATED' not in lines[i+1]

        finally:
            f_path.unlink()


class TestMarkerFormat:
    """Test marker formatting."""

    def test_format_marker(self):
        """Test marker string formatting."""
        info = AnnotationInfo(highlights=2, strokes=1)
        marker = format_marker(info)

        assert marker == "<!-- ANNOTATED: 2 highlights, 1 stroke -->"

    def test_format_marker_single_annotation(self):
        """Test marker with single annotation."""
        info = AnnotationInfo(highlights=1)
        marker = format_marker(info)

        assert marker == "<!-- ANNOTATED: 1 highlight -->"

    def test_format_marker_multiple_types(self):
        """Test marker with multiple annotation types."""
        info = AnnotationInfo(highlights=3, strokes=2, notes=1)
        marker = format_marker(info)

        assert "3 highlights" in marker
        assert "2 strokes" in marker
        assert "1 note" in marker


class TestMarkerStripping:
    """Test marker removal."""

    def test_strip_markers(self):
        """Test removing markers from content."""
        marked_content = """<!-- ANNOTATED: 2 highlights -->
Paragraph 1
<!-- /ANNOTATED -->

Paragraph 2

<!-- ANNOTATED: 1 stroke -->
Paragraph 3
<!-- /ANNOTATED -->
"""

        clean = strip_annotation_markers(marked_content)

        # Markers should be removed
        assert 'ANNOTATED' not in clean

        # Content should remain
        assert 'Paragraph 1' in clean
        assert 'Paragraph 2' in clean
        assert 'Paragraph 3' in clean

        # Should have reasonable paragraph spacing
        assert '\n\n' in clean

    def test_strip_markers_preserves_content(self):
        """Verify content is unchanged after stripping markers."""
        original_content = "# Header\n\nParagraph 1\n\nParagraph 2"

        with NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(original_content)
            f_path = Path(f.name)

        try:
            doc = parse_markdown_file(f_path)

            # Add markers
            annotation_map = {1: AnnotationInfo(highlights=1)}
            marked = add_annotation_markers_aligned(doc.content, annotation_map)

            # Strip markers
            clean = strip_annotation_markers(marked)

            # Parse both original and cleaned
            with NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f2:
                f2.write(clean)
                f2_path = Path(f2.name)

            try:
                doc_original = parse_markdown_file(f_path)
                doc_clean = parse_markdown_file(f2_path)

                # Should have same semantic hash
                from rock_paper_sync.hashing import compute_semantic_hash
                hash_original = compute_semantic_hash(doc_original.content)
                hash_clean = compute_semantic_hash(doc_clean.content)

                assert hash_original == hash_clean, "Content should be unchanged"

            finally:
                f2_path.unlink()

        finally:
            f_path.unlink()


class TestMarkerDetection:
    """Test marker detection utilities."""

    def test_has_annotation_markers(self):
        """Test detecting presence of markers."""
        marked = "<!-- ANNOTATED: 1 highlight -->\nText\n<!-- /ANNOTATED -->"
        unmarked = "Text without markers"

        assert has_annotation_markers(marked) is True
        assert has_annotation_markers(unmarked) is False

    def test_count_annotation_markers(self):
        """Test counting markers."""
        content_with_two = """<!-- ANNOTATED: 1 -->
Para1
<!-- /ANNOTATED -->

<!-- ANNOTATED: 2 -->
Para2
<!-- /ANNOTATED -->
"""

        assert count_annotation_markers(content_with_two) == 2

    def test_count_zero_markers(self):
        """Test counting with no markers."""
        content = "Regular markdown\n\nNo markers here"

        assert count_annotation_markers(content) == 0
