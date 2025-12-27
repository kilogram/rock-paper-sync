"""Transformation intents - WHAT, not HOW.

This module defines the interface between the domain layer (DocumentModel,
annotation routing logic) and the scene_adapter layer (rmscene block manipulation).

Callers express transformation intent through these types. The PageTransformExecutor
in scene_adapter handles all the rmscene mechanics internally.

Key design principles:
1. Intents are pure data - no rmscene imports in this module
2. The opaque_handle field carries Layer 2 data without Layer 1 knowing its type
3. PageTransformPlan is the ONLY input to PageTransformExecutor.execute()

Example:
    # In domain code (generator.py or similar):
    plan = PageTransformPlan(
        page_uuid="abc-123",
        page_text="Hello world",
        stroke_placements=[
            StrokePlacement(
                opaque_handle=bundle,  # StrokeBundle from extraction
                anchor_char_offset=0,
                source_page_idx=0,
            )
        ],
    )

    # In scene_adapter:
    executor = PageTransformExecutor(geometry)
    rm_bytes = executor.execute(plan)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TransformIntent:
    """Base class for all transformation intents.

    Subclasses represent specific operations like placing strokes,
    preserving unknown blocks, etc.
    """

    pass


@dataclass(frozen=True)
class StrokePlacement(TransformIntent):
    """Intent: place a stroke bundle at a specific anchor on a page.

    This is the declarative way to express "put this stroke here".
    The caller doesn't need to know about TreeNodeBlock, SceneTreeBlock,
    SceneGroupItemBlock, or any rmscene mechanics.

    Attributes:
        opaque_handle: The StrokeBundle (or similar) from extraction.
                      Domain code carries this without inspecting it.
        anchor_char_offset: Character offset in page text where the
                           stroke should be anchored.
        source_page_idx: Original page index (for cross-page detection).
        relative_y_offset: Y offset from anchor for relative positioning.
    """

    opaque_handle: Any  # StrokeBundle in scene_adapter layer
    anchor_char_offset: int
    source_page_idx: int | None = None
    relative_y_offset: float | None = None


@dataclass(frozen=True)
class HighlightPlacement(TransformIntent):
    """Intent: place a highlight at a specific text span on a page.

    Attributes:
        opaque_handle: The highlight block from extraction.
        start_offset: Start character offset in page text.
        end_offset: End character offset in page text.
    """

    opaque_handle: Any  # SceneGlyphItemBlock or similar
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class PreserveUnknown(TransformIntent):
    """Intent: preserve an unknown block verbatim.

    Unknown blocks are SACRED - we don't understand them, so we
    preserve them exactly as-is. This protects future pen types,
    new annotation styles, or any rmscene features we don't yet parse.

    Attributes:
        opaque_handle: The raw rmscene block to preserve.
    """

    opaque_handle: Any  # Raw rmscene block


@dataclass
class PageTransformPlan:
    """Complete transformation plan for generating a page's .rm file.

    This is the ONLY input needed by PageTransformExecutor. All rmscene
    mechanics are handled internally by the executor.

    The plan expresses WHAT should be on the page:
    - The text content (from markdown)
    - Which strokes to place and where
    - Which highlights to place and where
    - Which unknown blocks to preserve

    The executor handles HOW to implement this:
    - Regenerating structural blocks
    - Creating/updating TreeNodeBlocks with correct anchors
    - Maintaining scene graph integrity
    - Block ordering for device compatibility
    - Validation

    Attributes:
        page_uuid: UUID for this page
        page_text: Text content for the page (from markdown)
        stroke_placements: Strokes to place on this page
        highlight_placements: Highlights to place on this page
        unknown_blocks: Unknown blocks to preserve verbatim
        source_rm_path: Path to existing .rm file (for roundtrip extraction)
    """

    page_uuid: str
    page_text: str
    stroke_placements: list[StrokePlacement] = field(default_factory=list)
    highlight_placements: list[HighlightPlacement] = field(default_factory=list)
    unknown_blocks: list[PreserveUnknown] = field(default_factory=list)
    source_rm_path: Path | None = None
    first_line_is_heading: bool = False  # If True, use ParagraphStyle.HEADING

    @property
    def has_annotations(self) -> bool:
        """Check if this plan has any annotations to place."""
        return bool(self.stroke_placements or self.highlight_placements)

    @property
    def is_roundtrip(self) -> bool:
        """Check if this is a roundtrip (has source file to preserve from)."""
        return self.source_rm_path is not None and self.source_rm_path.exists()
