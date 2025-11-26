"""Tests for hashing and hash stability across annotation marker operations.

Tests ensure:
1. Semantic hash computation is correct and deterministic
2. Annotation markers don't affect semantic hash (prevents sync loops)
3. File hash vs semantic hash behavior is as expected
"""

import pytest
from pathlib import Path
from tempfile import NamedTemporaryFile

from rock_paper_sync.hashing import (
    compute_semantic_hash,
    compute_file_hash,
    compute_paragraph_hash,
)
from rock_paper_sync.parser import parse_markdown_file
from rock_paper_sync.annotation_mapper import AnnotationInfo
from rock_paper_sync.annotation_markers_v2 import (
    add_annotation_markers_aligned,
    strip_annotation_markers,
)


class TestSemanticHash:
    """Test semantic hash computation."""

    @pytest.fixture
    def temp_file(self):
        """Create a temporary markdown file for testing."""
        files = []

        def _create(content):
            f = NamedTemporaryFile(mode='w', suffix='.md', delete=False)
            f.write(content)
            f.close()
            path = Path(f.name)
            files.append(path)
            return path

        yield _create

        for path in files:
            path.unlink(missing_ok=True)

    def test_hash_ignores_extra_whitespace(self, temp_file):
        """Semantic hash should be identical with different whitespace."""
        doc1_path = temp_file("# Header\n\nParagraph text")
        doc2_path = temp_file("# Header\n\n\n\nParagraph text")

        doc1 = parse_markdown_file(doc1_path)
        doc2 = parse_markdown_file(doc2_path)

        assert compute_semantic_hash(doc1.content) == compute_semantic_hash(doc2.content)

    def test_hash_ignores_frontmatter(self, temp_file):
        """Semantic hash should ignore frontmatter changes."""
        doc1_path = temp_file("---\ntitle: Original\n---\n\n# Header\n\nParagraph")
        doc2_path = temp_file("---\ntitle: Modified\nauthor: Someone\n---\n\n# Header\n\nParagraph")

        doc1 = parse_markdown_file(doc1_path)
        doc2 = parse_markdown_file(doc2_path)

        assert compute_semantic_hash(doc1.content) == compute_semantic_hash(doc2.content)

    def test_hash_changes_on_content(self, temp_file):
        """Semantic hash should change when content changes."""
        doc1_path = temp_file("# Header\n\nParagraph text")
        doc2_path = temp_file("# Header\n\nDifferent paragraph text")

        doc1 = parse_markdown_file(doc1_path)
        doc2 = parse_markdown_file(doc2_path)

        assert compute_semantic_hash(doc1.content) != compute_semantic_hash(doc2.content)

    def test_hash_changes_on_structure(self, temp_file):
        """Semantic hash should change when document structure changes."""
        doc1_path = temp_file("# Header\n\nParagraph")
        doc2_path = temp_file("## Header\n\nParagraph")  # Different header level

        doc1 = parse_markdown_file(doc1_path)
        doc2 = parse_markdown_file(doc2_path)

        assert compute_semantic_hash(doc1.content) != compute_semantic_hash(doc2.content)

    def test_hash_changes_on_formatting(self, temp_file):
        """Semantic hash should change when inline formatting changes."""
        doc1_path = temp_file("Paragraph with text")
        doc2_path = temp_file("Paragraph with **bold** text")

        doc1 = parse_markdown_file(doc1_path)
        doc2 = parse_markdown_file(doc2_path)

        assert compute_semantic_hash(doc1.content) != compute_semantic_hash(doc2.content)

    def test_hash_deterministic(self, temp_file):
        """Same content should always produce same hash."""
        path = temp_file("# Header\n\nParagraph with **bold** and *italic* text.")
        doc = parse_markdown_file(path)

        hash1 = compute_semantic_hash(doc.content)
        hash2 = compute_semantic_hash(doc.content)
        hash3 = compute_semantic_hash(doc.content)

        assert hash1 == hash2 == hash3

    def test_hash_length(self, temp_file):
        """Hash should be 64 characters (SHA-256 hex digest)."""
        path = temp_file("# Test\n\nContent")
        doc = parse_markdown_file(path)
        hash_val = compute_semantic_hash(doc.content)

        assert len(hash_val) == 64
        assert all(c in '0123456789abcdef' for c in hash_val)


class TestFileHash:
    """Test file hash computation."""

    @pytest.fixture
    def temp_file(self):
        """Create a temporary markdown file for testing."""
        f = NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        f.write("Content")
        f.close()
        path = Path(f.name)
        yield path
        path.unlink(missing_ok=True)

    def test_file_hash_changes_on_any_modification(self, temp_file):
        """File hash should change on any modification."""
        hash1 = compute_file_hash(temp_file)

        temp_file.write_text("Content ")  # Added space
        hash2 = compute_file_hash(temp_file)

        assert hash1 != hash2

    def test_file_hash_identical_for_same_content(self):
        """File hash should be same for identical files."""
        content = "# Header\n\nParagraph"

        with NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f1:
            f1.write(content)
            f1_path = Path(f1.name)

        with NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f2:
            f2.write(content)
            f2_path = Path(f2.name)

        try:
            assert compute_file_hash(f1_path) == compute_file_hash(f2_path)
        finally:
            f1_path.unlink()
            f2_path.unlink()


class TestParagraphHash:
    """Test paragraph hash computation."""

    def test_paragraph_hash_ignores_whitespace(self):
        """Paragraph hash should ignore extra whitespace."""
        hash1 = compute_paragraph_hash("Hello world")
        hash2 = compute_paragraph_hash("Hello  world  ")

        assert hash1 == hash2

    def test_paragraph_hash_detects_content_change(self):
        """Paragraph hash should change when text changes."""
        hash1 = compute_paragraph_hash("Hello world")
        hash2 = compute_paragraph_hash("Goodbye world")

        assert hash1 != hash2


class TestMarkerHashStability:
    """CRITICAL: Test that annotation markers don't affect semantic hash.

    This prevents infinite sync loops where adding markers causes re-upload.
    """

    @pytest.fixture
    def markdown_file(self):
        """Create and cleanup a temporary markdown file."""
        f = NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        f.close()
        path = Path(f.name)
        yield path
        path.unlink(missing_ok=True)

    def test_semantic_hash_unchanged_after_adding_markers(self, markdown_file):
        """CRITICAL: Semantic hash must be unchanged when markers added."""
        markdown = """# Document Title

This is the first paragraph of the document.

This is the second paragraph with some content.

## Section Header

More content in a subsection.
"""
        markdown_file.write_text(markdown)

        # Parse and get semantic hash (initial sync)
        doc1 = parse_markdown_file(markdown_file)
        hash_initial = compute_semantic_hash(doc1.content)

        # Simulate: User annotates paragraphs on device
        annotation_map = {
            1: AnnotationInfo(highlights=2, strokes=1),
            3: AnnotationInfo(highlights=1)
        }

        # Add markers
        marked_content = add_annotation_markers_aligned(doc1.content, annotation_map)
        markdown_file.write_text(marked_content)

        # Parse again and get semantic hash
        doc2 = parse_markdown_file(markdown_file)
        hash_after_markers = compute_semantic_hash(doc2.content)

        assert hash_initial == hash_after_markers, \
            "Semantic hash MUST be unchanged after adding markers (prevents loop!)"

    def test_file_hash_changes_but_semantic_hash_stable(self, markdown_file):
        """File hash changes but semantic hash doesn't."""
        markdown_file.write_text("# Title\n\nParagraph")

        # Initial state
        file_hash_initial = compute_file_hash(markdown_file)
        doc1 = parse_markdown_file(markdown_file)
        semantic_hash_initial = compute_semantic_hash(doc1.content)

        # Add markers
        annotation_map = {1: AnnotationInfo(highlights=1)}
        marked = add_annotation_markers_aligned(doc1.content, annotation_map)
        markdown_file.write_text(marked)

        # After markers
        file_hash_after = compute_file_hash(markdown_file)
        doc2 = parse_markdown_file(markdown_file)
        semantic_hash_after = compute_semantic_hash(doc2.content)

        # File hash SHOULD change
        assert file_hash_initial != file_hash_after
        # Semantic hash should NOT change
        assert semantic_hash_initial == semantic_hash_after

    def test_multiple_marker_additions_idempotent(self, markdown_file):
        """Adding markers multiple times should not change semantic hash."""
        markdown_file.write_text("# Title\n\nParagraph 1\n\nParagraph 2")

        doc = parse_markdown_file(markdown_file)
        hash_original = compute_semantic_hash(doc.content)

        annotation_map = {1: AnnotationInfo(highlights=1)}

        # Add markers first time
        marked1 = add_annotation_markers_aligned(doc.content, annotation_map)
        markdown_file.write_text(marked1)
        doc1 = parse_markdown_file(markdown_file)
        hash1 = compute_semantic_hash(doc1.content)

        # Add markers second time
        marked2 = add_annotation_markers_aligned(doc1.content, annotation_map)
        markdown_file.write_text(marked2)
        doc2 = parse_markdown_file(markdown_file)
        hash2 = compute_semantic_hash(doc2.content)

        assert hash_original == hash1 == hash2

    def test_strip_and_readd_markers_preserves_hash(self, markdown_file):
        """Stripping and re-adding markers should not change semantic hash."""
        markdown_file.write_text("# Document\n\nContent here")

        doc_original = parse_markdown_file(markdown_file)
        hash_original = compute_semantic_hash(doc_original.content)

        annotation_map = {1: AnnotationInfo(highlights=2)}

        # Add markers
        marked = add_annotation_markers_aligned(doc_original.content, annotation_map)
        markdown_file.write_text(marked)
        doc_marked = parse_markdown_file(markdown_file)
        hash_marked = compute_semantic_hash(doc_marked.content)

        # Strip markers
        clean = strip_annotation_markers(marked)
        markdown_file.write_text(clean)
        doc_clean = parse_markdown_file(markdown_file)
        hash_clean = compute_semantic_hash(doc_clean.content)

        # Re-add markers
        marked_again = add_annotation_markers_aligned(doc_clean.content, annotation_map)
        markdown_file.write_text(marked_again)
        doc_remarked = parse_markdown_file(markdown_file)
        hash_remarked = compute_semantic_hash(doc_remarked.content)

        assert hash_original == hash_marked == hash_clean == hash_remarked

    def test_hash_stable_with_various_marker_formats(self, markdown_file):
        """Hash should be stable regardless of marker annotation counts."""
        content = "# Test\n\nAnnotated paragraph."
        markdown_file.write_text(content)

        original_hash = parse_markdown_file(markdown_file).content_hash

        test_cases = [
            {1: AnnotationInfo(highlights=1)},
            {1: AnnotationInfo(highlights=100, strokes=50, notes=25)},
            {1: AnnotationInfo(strokes=1)},
        ]

        for annotation_map in test_cases:
            doc = parse_markdown_file(markdown_file)
            marked = add_annotation_markers_aligned(doc.content, annotation_map)
            markdown_file.write_text(marked)
            new_hash = parse_markdown_file(markdown_file).content_hash
            assert new_hash == original_hash, f"Hash changed with {annotation_map}"

            # Reset for next test
            markdown_file.write_text(content)


class TestSyncScenarios:
    """Test realistic sync scenarios."""

    @pytest.fixture
    def markdown_file(self):
        """Create and cleanup a temporary markdown file."""
        f = NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        f.close()
        path = Path(f.name)
        yield path
        path.unlink(missing_ok=True)

    def test_user_annotates_then_sync_twice(self, markdown_file):
        """Simulate: user annotates → sync → sync again (should skip second)."""
        markdown = """# My Notes

Important paragraph about topic A.

Another paragraph about topic B.

Final thoughts on topic C.
"""
        markdown_file.write_text(markdown)

        # Sync 1: Initial upload
        doc_sync1 = parse_markdown_file(markdown_file)
        hash_sync1 = compute_semantic_hash(doc_sync1.content)

        # User annotates on device, sync 2 adds markers
        annotations_from_device = {
            1: AnnotationInfo(highlights=2),
            2: AnnotationInfo(highlights=1)
        }
        marked_content = add_annotation_markers_aligned(
            doc_sync1.content,
            annotations_from_device
        )
        markdown_file.write_text(marked_content)

        doc_sync2 = parse_markdown_file(markdown_file)
        hash_sync2 = compute_semantic_hash(doc_sync2.content)

        # Sync 3: Should detect no changes
        doc_sync3 = parse_markdown_file(markdown_file)
        hash_sync3 = compute_semantic_hash(doc_sync3.content)

        assert hash_sync1 == hash_sync2 == hash_sync3

    def test_user_edits_after_annotations(self, markdown_file):
        """Simulate: annotate → sync → user edits → sync (should detect change)."""
        markdown_file.write_text("# Title\n\nOriginal content")

        # Sync 1: Initial
        doc1 = parse_markdown_file(markdown_file)
        hash1 = compute_semantic_hash(doc1.content)

        # Sync 2: Add markers
        annotation_map = {1: AnnotationInfo(highlights=1)}
        marked = add_annotation_markers_aligned(doc1.content, annotation_map)
        markdown_file.write_text(marked)
        doc2 = parse_markdown_file(markdown_file)
        hash2 = compute_semantic_hash(doc2.content)

        assert hash1 == hash2

        # User edits content
        edited = marked.replace("Original content", "Modified content")
        markdown_file.write_text(edited)

        doc3 = parse_markdown_file(markdown_file)
        hash3 = compute_semantic_hash(doc3.content)

        assert hash2 != hash3

    def test_empty_annotation_map(self, markdown_file):
        """Empty annotation map should not change hash."""
        markdown_file.write_text("# Title\n\nContent")

        doc = parse_markdown_file(markdown_file)
        hash_before = compute_semantic_hash(doc.content)

        marked = add_annotation_markers_aligned(doc.content, {})
        markdown_file.write_text(marked)

        doc_after = parse_markdown_file(markdown_file)
        hash_after = compute_semantic_hash(doc_after.content)

        assert hash_before == hash_after

    def test_markers_with_frontmatter(self, markdown_file):
        """Markers should work correctly with frontmatter."""
        markdown = """---
title: Document
tags: [important]
---

# Content

Paragraph
"""
        markdown_file.write_text(markdown)

        doc = parse_markdown_file(markdown_file)
        hash_before = compute_semantic_hash(doc.content)

        annotation_map = {1: AnnotationInfo(highlights=1)}
        marked = add_annotation_markers_aligned(doc.content, annotation_map)

        # Reconstruct with frontmatter
        full_content = "---\ntitle: Document\ntags: [important]\n---\n\n" + marked
        markdown_file.write_text(full_content)

        doc_after = parse_markdown_file(markdown_file)
        hash_after = compute_semantic_hash(doc_after.content)

        assert hash_before == hash_after
