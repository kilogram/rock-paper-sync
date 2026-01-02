"""Consolidated .rm file reading and extraction.

This module provides a single point of truth for reading and extracting data
from reMarkable .rm files. It consolidates logic that was previously scattered
across multiple modules:

- generator.py:_extract_text_blocks_from_rm()
- document_model.py:from_rm_files()
- layout/context.py:from_rm_file()
- coordinates.py:AnchorResolver.from_rm_file()
- scene_adapter/scene_index.py:SceneGraphIndex.from_file()

Usage:
    # Read an .rm file
    extractor = RmFileExtractor.from_path(rm_path)

    # Access extracted data
    text = extractor.text_content
    origin = extractor.text_origin
    blocks = extractor.blocks

    # Create higher-level objects
    layout_ctx = extractor.get_layout_context(geometry)
    scene_index = extractor.get_scene_index()

Architecture note:
    This is the low-level extraction layer. Higher-level abstractions like
    DocumentModel, LayoutContext, and AnchorResolver should delegate to
    this module for .rm file reading.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import rmscene

if TYPE_CHECKING:
    from rmscene.tagged_block_common import CrdtId

    from rock_paper_sync.annotations.core.types import TextBlock
    from rock_paper_sync.annotations.scene_adapter.scene_index import SceneGraphIndex
    from rock_paper_sync.layout import LayoutContext
    from rock_paper_sync.layout.device import DeviceGeometry


logger = logging.getLogger(__name__)


@dataclass
class TextOriginInfo:
    """Text origin and dimensions from RootTextBlock."""

    pos_x: float
    pos_y: float
    width: float

    def as_tuple(self) -> tuple[float, float, float]:
        """Return (pos_x, pos_y, width) tuple."""
        return (self.pos_x, self.pos_y, self.width)


@dataclass
class RmFileExtractor:
    """Consolidated .rm file reader and data extractor.

    Reads an .rm file once and provides access to all extracted data.
    Caches computed values for efficiency.

    Attributes:
        blocks: Raw rmscene blocks from the file
        text_content: Full text content from RootTextBlock
        text_origin: Text origin and dimensions
        crdt_to_char: Mapping from CRDT IDs to character offsets

    Example:
        >>> extractor = RmFileExtractor.from_path(Path("page.rm"))
        >>> print(f"Text: {extractor.text_content[:50]}...")
        >>> layout = extractor.get_layout_context(DEFAULT_DEVICE)
    """

    blocks: list[Any]
    text_content: str = ""
    text_origin: TextOriginInfo = field(
        default_factory=lambda: TextOriginInfo(pos_x=234.0, pos_y=94.0, width=750.0)
    )
    crdt_to_char: dict[CrdtId, int] = field(default_factory=dict)

    # Cached objects (lazily computed)
    _scene_index: SceneGraphIndex | None = field(default=None, repr=False)
    _source_path: Path | None = field(default=None, repr=False)

    @classmethod
    def from_path(cls, rm_path: Path) -> RmFileExtractor:
        """Create extractor from .rm file path.

        Args:
            rm_path: Path to .rm file

        Returns:
            RmFileExtractor with all data extracted

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file is not a valid .rm file
        """
        try:
            with rm_path.open("rb") as f:
                blocks = list(rmscene.read_blocks(f))
        except Exception as e:
            logger.warning(f"Failed to read .rm file {rm_path}: {e}")
            raise ValueError(f"Failed to read .rm file: {e}") from e

        extractor = cls._from_blocks_internal(blocks)
        extractor._source_path = rm_path
        return extractor

    @classmethod
    def from_bytes(cls, rm_bytes: bytes) -> RmFileExtractor:
        """Create extractor from .rm file bytes.

        Args:
            rm_bytes: Raw bytes of .rm file content

        Returns:
            RmFileExtractor with all data extracted

        Raises:
            ValueError: If bytes are not valid .rm content
        """
        try:
            blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        except Exception as e:
            logger.warning(f"Failed to parse .rm bytes: {e}")
            raise ValueError(f"Failed to parse .rm bytes: {e}") from e

        return cls._from_blocks_internal(blocks)

    @classmethod
    def from_blocks(cls, blocks: list[Any]) -> RmFileExtractor:
        """Create extractor from pre-read rmscene blocks.

        Use this when you already have blocks from rmscene.read_blocks()
        to avoid re-reading the file.

        Args:
            blocks: List of rmscene blocks

        Returns:
            RmFileExtractor with all data extracted
        """
        return cls._from_blocks_internal(blocks)

    @classmethod
    def _from_blocks_internal(cls, blocks: list[Any]) -> RmFileExtractor:
        """Internal constructor from blocks.

        Extracts text content, origin, and CRDT mapping from blocks.
        """
        from rmscene.tagged_block_common import CrdtId

        text_content = ""
        pos_x = 234.0  # Default from geometry
        pos_y = 94.0
        width = 750.0
        crdt_to_char: dict[CrdtId, int] = {}

        # Find RootTextBlock and extract text
        for block in blocks:
            if "RootText" in type(block).__name__:
                text_data = block.value
                pos_x = text_data.pos_x
                pos_y = text_data.pos_y
                width = text_data.width

                # Extract text from CrdtSequence and build CRDT ID mapping
                text_parts = []
                char_offset = 0
                for item in text_data.items.sequence_items():
                    if hasattr(item, "value") and isinstance(item.value, str):
                        text = item.value
                        item_id = item.item_id
                        # Map each character's CRDT ID to its offset
                        for i in range(len(text)):
                            char_crdt_id = CrdtId(item_id.part1, item_id.part2 + i)
                            crdt_to_char[char_crdt_id] = char_offset + i
                        text_parts.append(text)
                        char_offset += len(text)
                text_content = "".join(text_parts)
                break

        return cls(
            blocks=blocks,
            text_content=text_content,
            text_origin=TextOriginInfo(pos_x=pos_x, pos_y=pos_y, width=width),
            crdt_to_char=crdt_to_char,
        )

    def get_scene_index(self) -> SceneGraphIndex:
        """Get scene graph index (cached).

        Returns:
            SceneGraphIndex for efficient block lookups
        """
        if self._scene_index is None:
            from rock_paper_sync.annotations.scene_adapter.scene_index import (
                SceneGraphIndex,
            )

            self._scene_index = SceneGraphIndex.from_blocks(self.blocks)
        return self._scene_index

    def get_layout_context(
        self,
        geometry: DeviceGeometry | None = None,
        use_font_metrics: bool = True,
    ) -> LayoutContext:
        """Create LayoutContext from extracted data.

        Args:
            geometry: Device geometry (uses DEFAULT_DEVICE if not provided)
            use_font_metrics: Whether to use Noto Sans font metrics

        Returns:
            LayoutContext for position calculations
        """
        from rock_paper_sync.layout import DEFAULT_DEVICE, LayoutContext, TextAreaConfig

        effective_geometry = geometry or DEFAULT_DEVICE

        config = TextAreaConfig(
            text_width=self.text_origin.width,
            text_pos_x=self.text_origin.pos_x,
            text_pos_y=self.text_origin.pos_y,
            line_height=effective_geometry.line_height,
            char_width=effective_geometry.char_width,
        )

        return LayoutContext.from_text(
            self.text_content, use_font_metrics, config, effective_geometry
        )

    def get_text_blocks(self, geometry: DeviceGeometry | None = None) -> list[TextBlock]:
        """Extract text blocks with Y positions.

        Creates TextBlock objects for each paragraph with position information.
        This consolidates the logic from generator._extract_text_blocks_from_rm().

        Args:
            geometry: Device geometry for layout calculations

        Returns:
            List of TextBlock objects with position information
        """
        from rock_paper_sync.annotations.core.types import TextBlock
        from rock_paper_sync.layout import DEFAULT_DEVICE

        effective_geometry = geometry or DEFAULT_DEVICE

        if not self.text_content:
            return []

        layout_ctx = self.get_layout_context(effective_geometry, use_font_metrics=True)
        paragraphs = self.text_content.split("\n")
        text_blocks: list[TextBlock] = []

        current_offset = 0
        for paragraph in paragraphs:
            if paragraph.strip():
                # Find paragraph start/end in full text
                para_start = self.text_content.find(paragraph, current_offset)
                if para_start == -1:
                    para_start = current_offset
                para_end = para_start + len(paragraph)
                current_offset = para_end + 1  # +1 for newline

                # Get Y positions from layout engine
                _, y_start = layout_ctx.offset_to_position(para_start)
                _, y_end = layout_ctx.offset_to_position(para_end)
                # Add one line height since offset_to_position gives the TOP of the line
                y_end += layout_ctx.line_height

                text_blocks.append(
                    TextBlock(
                        content=paragraph,
                        y_start=y_start,
                        y_end=y_end,
                        block_type="paragraph",
                        char_start=para_start,
                        char_end=para_end,
                    )
                )

        return text_blocks

    @property
    def source_path(self) -> Path | None:
        """Return the source file path, if available."""
        return self._source_path

    @property
    def is_empty(self) -> bool:
        """Return True if no text content was extracted."""
        return not self.text_content

    def __repr__(self) -> str:
        path_str = f", path={self._source_path}" if self._source_path else ""
        return (
            f"RmFileExtractor(text_len={len(self.text_content)}, "
            f"blocks={len(self.blocks)}, "
            f"origin=({self.text_origin.pos_x:.1f}, {self.text_origin.pos_y:.1f})"
            f"{path_str})"
        )
