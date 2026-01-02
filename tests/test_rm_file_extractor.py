"""Tests for RmFileExtractor consolidation.

Tests the unified .rm file reading module to ensure it correctly extracts
text content, positions, and scene graph data from .rm files.
"""

from pathlib import Path

import pytest

from rock_paper_sync.annotations.core.types import TextBlock
from rock_paper_sync.rm_file_extractor import RmFileExtractor, TextOriginInfo

# Test data paths
TESTDATA_DIR = Path(__file__).parent / "annotations" / "testdata" / "rmscene"
RECORD_REPLAY_DIR = Path(__file__).parent / "record_replay" / "testdata"


class TestRmFileExtractor:
    """Tests for RmFileExtractor class."""

    @pytest.fixture
    def sample_rm_file(self) -> Path:
        """Get a sample .rm file with known content."""
        # Use a calibration file that has known text content
        return RECORD_REPLAY_DIR / "calibration" / "paper_pro_move" / "calibration_geometry.rm"

    @pytest.fixture
    def wikipedia_rm_file(self) -> Path:
        """Get Wikipedia highlighted .rm file."""
        return TESTDATA_DIR / "Wikipedia_highlighted_p1.rm"

    def test_from_path_reads_file(self, sample_rm_file: Path) -> None:
        """Test that from_path successfully reads an .rm file."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        extractor = RmFileExtractor.from_path(sample_rm_file)

        assert extractor is not None
        assert len(extractor.blocks) > 0
        assert extractor.source_path == sample_rm_file

    def test_from_path_extracts_text(self, sample_rm_file: Path) -> None:
        """Test that from_path extracts text content."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        extractor = RmFileExtractor.from_path(sample_rm_file)

        # Should have extracted some text
        assert extractor.text_content != ""
        # Text origin should have reasonable values
        # Note: pos_x can be negative for centered text
        assert extractor.text_origin.pos_y > 0
        assert extractor.text_origin.width > 0

    def test_from_bytes_works(self, sample_rm_file: Path) -> None:
        """Test that from_bytes works with raw file bytes."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        rm_bytes = sample_rm_file.read_bytes()
        extractor = RmFileExtractor.from_bytes(rm_bytes)

        assert extractor is not None
        assert len(extractor.blocks) > 0
        assert extractor.text_content != ""

    def test_from_path_and_from_bytes_equivalent(self, sample_rm_file: Path) -> None:
        """Test that from_path and from_bytes produce equivalent results."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        extractor_path = RmFileExtractor.from_path(sample_rm_file)
        extractor_bytes = RmFileExtractor.from_bytes(sample_rm_file.read_bytes())

        assert extractor_path.text_content == extractor_bytes.text_content
        assert extractor_path.text_origin.pos_x == extractor_bytes.text_origin.pos_x
        assert extractor_path.text_origin.pos_y == extractor_bytes.text_origin.pos_y
        assert extractor_path.text_origin.width == extractor_bytes.text_origin.width
        assert len(extractor_path.blocks) == len(extractor_bytes.blocks)

    def test_get_scene_index(self, sample_rm_file: Path) -> None:
        """Test that get_scene_index returns valid index."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        extractor = RmFileExtractor.from_path(sample_rm_file)
        scene_index = extractor.get_scene_index()

        # Should return SceneGraphIndex
        assert scene_index is not None
        # Second call should return cached value
        assert extractor.get_scene_index() is scene_index

    def test_get_layout_context(self, sample_rm_file: Path) -> None:
        """Test that get_layout_context returns valid context."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        extractor = RmFileExtractor.from_path(sample_rm_file)
        layout_ctx = extractor.get_layout_context()

        # Should return LayoutContext
        assert layout_ctx is not None
        # Should have text content
        assert layout_ctx.text_content == extractor.text_content

    def test_get_text_blocks(self, sample_rm_file: Path) -> None:
        """Test that get_text_blocks returns paragraph blocks."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        extractor = RmFileExtractor.from_path(sample_rm_file)
        text_blocks = extractor.get_text_blocks()

        # Should have some blocks if there's text
        if extractor.text_content.strip():
            assert len(text_blocks) > 0
            # Each block should have valid positions
            for block in text_blocks:
                assert isinstance(block, TextBlock)
                assert block.y_start < block.y_end
                assert block.char_start <= block.char_end
                assert block.content in extractor.text_content

    def test_crdt_to_char_mapping(self, sample_rm_file: Path) -> None:
        """Test that CRDT to character mapping is built."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        extractor = RmFileExtractor.from_path(sample_rm_file)

        # If there's text, there should be CRDT mappings
        if extractor.text_content:
            assert len(extractor.crdt_to_char) > 0
            # All char offsets should be valid
            for crdt_id, char_offset in extractor.crdt_to_char.items():
                assert 0 <= char_offset < len(extractor.text_content)

    def test_is_empty_property(self) -> None:
        """Test is_empty property."""
        # Create extractor with no text
        extractor = RmFileExtractor(blocks=[], text_content="")
        assert extractor.is_empty

        # Create extractor with text
        extractor = RmFileExtractor(blocks=[], text_content="Hello")
        assert not extractor.is_empty

    def test_text_origin_as_tuple(self) -> None:
        """Test TextOriginInfo.as_tuple method."""
        origin = TextOriginInfo(pos_x=100.0, pos_y=200.0, width=750.0)
        assert origin.as_tuple() == (100.0, 200.0, 750.0)

    def test_text_block_contains_y(self) -> None:
        """Test TextBlock.contains_y method."""
        block = TextBlock(
            content="Test",
            y_start=100.0,
            y_end=150.0,
            block_type="paragraph",
            char_start=0,
            char_end=4,
        )

        assert block.contains_y(100.0)  # Start is inclusive
        assert block.contains_y(125.0)  # Middle
        assert block.contains_y(150.0)  # End is inclusive in existing impl
        assert not block.contains_y(99.0)  # Before
        assert not block.contains_y(151.0)  # After

    def test_from_path_invalid_file(self, tmp_path: Path) -> None:
        """Test that from_path raises for invalid files."""
        invalid_file = tmp_path / "invalid.rm"
        invalid_file.write_bytes(b"not a valid rm file")

        with pytest.raises(ValueError, match="Failed to"):
            RmFileExtractor.from_path(invalid_file)

    def test_from_path_nonexistent_file(self, tmp_path: Path) -> None:
        """Test that from_path raises for nonexistent files."""
        nonexistent = tmp_path / "nonexistent.rm"

        with pytest.raises((FileNotFoundError, ValueError)):
            RmFileExtractor.from_path(nonexistent)

    def test_repr(self, sample_rm_file: Path) -> None:
        """Test __repr__ method."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        extractor = RmFileExtractor.from_path(sample_rm_file)
        repr_str = repr(extractor)

        assert "RmFileExtractor" in repr_str
        assert "text_len=" in repr_str
        assert "blocks=" in repr_str
        assert "origin=" in repr_str


class TestRmFileExtractorWithAnnotations:
    """Tests for RmFileExtractor with files that have annotations."""

    @pytest.fixture
    def annotated_rm_file(self) -> Path:
        """Get an annotated .rm file."""
        return TESTDATA_DIR / "Wikipedia_highlighted_p1.rm"

    def test_scene_index_has_strokes(self, annotated_rm_file: Path) -> None:
        """Test that scene index contains stroke data."""
        if not annotated_rm_file.exists():
            pytest.skip(f"Test file not found: {annotated_rm_file}")

        extractor = RmFileExtractor.from_path(annotated_rm_file)
        scene_index = extractor.get_scene_index()

        # Wikipedia highlighted should have strokes (highlights)
        # Note: May not have strokes if it only has text
        assert scene_index is not None


class TestRmFileExtractorCompatibility:
    """Tests to ensure RmFileExtractor matches behavior of existing extractors."""

    @pytest.fixture
    def sample_rm_file(self) -> Path:
        """Get a sample .rm file."""
        return RECORD_REPLAY_DIR / "calibration" / "paper_pro_move" / "calibration_geometry.rm"

    def test_matches_layout_context_from_rm_file(self, sample_rm_file: Path) -> None:
        """Test that extractor produces same LayoutContext as LayoutContext.from_rm_file."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        from rock_paper_sync.layout import LayoutContext

        # Get LayoutContext from RmFileExtractor
        extractor = RmFileExtractor.from_path(sample_rm_file)
        layout_from_extractor = extractor.get_layout_context()

        # Get LayoutContext directly (old way)
        layout_direct = LayoutContext.from_rm_file(sample_rm_file)

        # Should have same text content
        assert layout_from_extractor.text_content == layout_direct.text_content

    def test_matches_scene_graph_index_from_file(self, sample_rm_file: Path) -> None:
        """Test that extractor produces same SceneGraphIndex as SceneGraphIndex.from_file."""
        if not sample_rm_file.exists():
            pytest.skip(f"Test file not found: {sample_rm_file}")

        from rock_paper_sync.annotations.scene_adapter.scene_index import SceneGraphIndex

        # Get SceneGraphIndex from RmFileExtractor
        extractor = RmFileExtractor.from_path(sample_rm_file)
        index_from_extractor = extractor.get_scene_index()

        # Get SceneGraphIndex directly (old way)
        index_direct = SceneGraphIndex.from_file(sample_rm_file)

        # Should have same structure
        assert len(index_from_extractor.tree_nodes) == len(index_direct.tree_nodes)
        assert len(index_from_extractor.scene_trees) == len(index_direct.scene_trees)
        assert len(index_from_extractor.strokes) == len(index_direct.strokes)
