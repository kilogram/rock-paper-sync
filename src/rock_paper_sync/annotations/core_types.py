"""
Annotation extraction and preservation for reMarkable documents.

This module handles reading annotations (strokes and highlights) from reMarkable .rm files
and associating them with markdown content for preservation during sync operations.

Architecture:
    [.rm file] → [read_annotations()] → [Annotation objects] → [associate_with_content()]
         ↓                                      ↓                         ↓
    rmscene blocks                    position + data              content blocks

Key Concepts:
    - **Strokes**: Hand-drawn annotations (lines, sketches) with point coordinates
    - **Highlights**: Text selections with bounding rectangles
    - **Position mapping**: Annotations are mapped to Y-coordinates on the page
    - **Content association**: Annotations are linked to nearby text blocks
    - **Preservation**: When regenerating, annotations are repositioned relative to content

Position Coordinate System:
    reMarkable uses a coordinate system where:
    - (0, 0) is typically near the center of the page
    - Positive Y goes downward
    - Text blocks have pos_x, pos_y, and width
    - Page height ~1872 points (A4 at 226 DPI)

Example:
    # Extract annotations from existing .rm file
    annotations = read_annotations("document.rm")

    # Associate with markdown content
    mapping = associate_annotations_with_content(annotations, content_blocks)

    # Preserve when regenerating
    preserved_strokes = preserve_strokes(mapping, old_blocks, new_blocks)
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import BinaryIO

import rmscene as rm
from rmscene.tagged_block_common import CrdtId


class AnnotationType(Enum):
    """Type of annotation."""

    STROKE = "stroke"  # Hand-drawn lines/sketches
    HIGHLIGHT = "highlight"  # Text highlights


@dataclass
class Point:
    """A point in 2D space with optional metadata."""

    x: float
    y: float
    pressure: float | None = None
    width: int | None = None
    speed: float | None = None


@dataclass
class Rectangle:
    """A rectangular region on the page."""

    x: float
    y: float
    w: float
    h: float

    def center_y(self) -> float:
        """Get the vertical center of the rectangle."""
        return self.y + self.h / 2

    def contains_point(self, x: float, y: float) -> bool:
        """Check if a point is within this rectangle."""
        return self.x <= x <= self.x + self.w and self.y <= y <= self.y + self.h


@dataclass
class Stroke:
    """A hand-drawn stroke annotation.

    Attributes:
        points: List of points forming the stroke path
        color: Pen color code (0=black, 1=grey, 2=white, 3=yellow, etc.)
        tool: Pen tool type (ballpoint, fineliner, highlighter, etc.)
        thickness: Stroke thickness scale
        bounding_box: Axis-aligned bounding box containing all points
    """

    points: list[Point]
    color: int
    tool: int
    thickness: float
    bounding_box: Rectangle = field(init=False)

    def __post_init__(self):
        """Calculate bounding box from points."""
        if not self.points:
            self.bounding_box = Rectangle(0, 0, 0, 0)
        else:
            xs = [p.x for p in self.points]
            ys = [p.y for p in self.points]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            self.bounding_box = Rectangle(min_x, min_y, max_x - min_x, max_y - min_y)

    def center_y(self) -> float:
        """Get the vertical center of the stroke."""
        return self.bounding_box.center_y()


@dataclass
class Highlight:
    """A text highlight annotation.

    Attributes:
        text: The highlighted text content
        color: Highlight color code
        rectangles: List of rectangles covering the highlight (can span multiple lines)
    """

    text: str
    color: int
    rectangles: list[Rectangle]

    def center_y(self) -> float:
        """Get the average vertical center of all rectangles."""
        if not self.rectangles:
            return 0.0
        return sum(r.center_y() for r in self.rectangles) / len(self.rectangles)


@dataclass
class Annotation:
    """A generic annotation that can be either a stroke or highlight.

    Attributes:
        type: The type of annotation
        stroke: Stroke data (if type is STROKE)
        highlight: Highlight data (if type is HIGHLIGHT)
        layer_id: Optional layer/group ID for organization
        parent_id: Optional parent CRDT ID for coordinate space detection
            - CrdtId(0, 11): Absolute coordinates (root layer)
            - Other values: Text-relative coordinates
        annotation_id: Unique identifier for this annotation
    """

    type: AnnotationType
    stroke: Stroke | None = None
    highlight: Highlight | None = None
    layer_id: str | None = None
    parent_id: CrdtId | None = None
    annotation_id: str = field(default_factory=lambda: str(id(None)))  # Unique ID per instance

    def center_y(self) -> float:
        """Get the vertical center position of this annotation."""
        if self.type == AnnotationType.STROKE and self.stroke:
            return self.stroke.center_y()
        elif self.type == AnnotationType.HIGHLIGHT and self.highlight:
            return self.highlight.center_y()
        return 0.0

    @property
    def bounding_box(self) -> Rectangle | None:
        """Get bounding box for this annotation."""
        if self.stroke:
            return self.stroke.bounding_box
        elif self.highlight and self.highlight.rectangles:
            # Return the combined bounding box of all highlight rectangles
            if not self.highlight.rectangles:
                return None
            rects = self.highlight.rectangles
            return Rectangle(
                x=min(r.x for r in rects),
                y=min(r.y for r in rects),
                w=max(r.x + r.w for r in rects) - min(r.x for r in rects),
                h=max(r.y + r.h for r in rects) - min(r.y for r in rects),
            )
        return None


@dataclass
class TextBlock:
    """A block of text content with position information.

    This represents text generated from markdown (headings, paragraphs, lists, etc.)
    that we want to associate with annotations.

    Attributes:
        content: The text content
        y_start: Starting Y coordinate
        y_end: Ending Y coordinate
        block_type: Type of block (heading, paragraph, list_item, etc.)
        markdown_line: Optional line number in original markdown
    """

    content: str
    y_start: float
    y_end: float
    block_type: str
    markdown_line: int | None = None

    def contains_y(self, y: float) -> bool:
        """Check if a Y coordinate falls within this text block."""
        return self.y_start <= y <= self.y_end


@dataclass
class AnnotationMapping:
    """Maps annotations to their associated text blocks.

    Attributes:
        annotations: All annotations in the document
        text_blocks: All text blocks in the document
        associations: Dict mapping annotation index to text block index
    """

    annotations: list[Annotation]
    text_blocks: list[TextBlock]
    associations: dict[int, int] = field(default_factory=dict)


def read_annotations(file_path: Path | str | BinaryIO) -> list[Annotation]:
    """Read all annotations from a reMarkable .rm file.

    Args:
        file_path: Path to .rm file or open binary file object

    Returns:
        List of Annotation objects (strokes and highlights)

    Example:
        >>> annotations = read_annotations("document.rm")
        >>> print(f"Found {len(annotations)} annotations")
        >>> for ann in annotations:
        ...     if ann.type == AnnotationType.STROKE:
        ...         print(f"Stroke at y={ann.stroke.center_y()}")
    """
    annotations = []

    # Open file if path provided
    if isinstance(file_path, Path | str):
        with open(file_path, "rb") as f:
            return read_annotations(f)

    # Read blocks from file
    blocks = list(rm.read_blocks(file_path))

    # Extract strokes (hand-drawn annotations)
    for block in blocks:
        if "Line" in type(block).__name__:
            line = block.item.value

            # Skip if line is None or has no points
            if line is None or not hasattr(line, "points") or line.points is None:
                continue

            # Convert rmscene points to our Point objects
            points = [
                Point(
                    x=p.x,
                    y=p.y,
                    pressure=p.pressure if hasattr(p, "pressure") else 100,
                    width=p.width if hasattr(p, "width") else 16,
                    speed=p.speed if hasattr(p, "speed") else 0,
                )
                for p in line.points
            ]

            # Skip strokes with no points
            if not points:
                continue

            stroke = Stroke(
                points=points,
                color=line.color.value if hasattr(line.color, "value") else line.color,
                tool=line.tool.value if hasattr(line.tool, "value") else line.tool,
                thickness=line.thickness_scale if hasattr(line, "thickness_scale") else 2.0,
            )

            # Extract parent_id for coordinate space detection
            parent_id = block.parent_id if hasattr(block, "parent_id") else None

            annotations.append(
                Annotation(type=AnnotationType.STROKE, stroke=stroke, parent_id=parent_id)
            )

    # Extract highlights (text selections)
    for block in blocks:
        if "Glyph" in type(block).__name__:
            glyph = block.item.value

            # Skip if glyph is None or has no rectangles
            if glyph is None or not hasattr(glyph, "rectangles") or glyph.rectangles is None:
                continue

            # Convert rectangles
            rectangles = [Rectangle(x=r.x, y=r.y, w=r.w, h=r.h) for r in glyph.rectangles]

            # Skip if no valid rectangles
            if not rectangles:
                continue

            highlight = Highlight(
                text=glyph.text if hasattr(glyph, "text") and glyph.text else "",
                color=glyph.color.value if hasattr(glyph.color, "value") else glyph.color,
                rectangles=rectangles,
            )

            # Extract parent_id for coordinate space detection
            parent_id = block.parent_id if hasattr(block, "parent_id") else None

            annotations.append(
                Annotation(type=AnnotationType.HIGHLIGHT, highlight=highlight, parent_id=parent_id)
            )

    return annotations


def associate_annotations_with_content(
    annotations: list[Annotation], text_blocks: list[TextBlock], max_distance: float = 100.0
) -> AnnotationMapping:
    """Associate annotations with nearby text blocks based on position.

    Uses a simple proximity heuristic: annotations are associated with the nearest
    text block within max_distance. This works well for common use cases like:
    - Underlining text with a stroke
    - Highlighting a passage
    - Adding margin notes near paragraphs

    Args:
        annotations: List of annotations to associate
        text_blocks: List of text blocks to associate with
        max_distance: Maximum vertical distance for association (default: 100 points)

    Returns:
        AnnotationMapping with associations dict populated

    Example:
        >>> mapping = associate_annotations_with_content(annotations, blocks)
        >>> for ann_idx, block_idx in mapping.associations.items():
        ...     ann = mapping.annotations[ann_idx]
        ...     block = mapping.text_blocks[block_idx]
        ...     print(f"Annotation at y={ann.center_y()} → '{block.content[:30]}'")
    """
    mapping = AnnotationMapping(annotations=annotations, text_blocks=text_blocks)

    # For each annotation, find the nearest text block
    for ann_idx, annotation in enumerate(annotations):
        ann_y = annotation.center_y()

        # Find closest text block
        min_distance = float("inf")
        closest_block_idx = None

        for block_idx, block in enumerate(text_blocks):
            # Calculate distance (considering block range)
            if block.contains_y(ann_y):
                distance = 0.0  # Inside the block
            else:
                # Distance to nearest edge
                distance = min(abs(ann_y - block.y_start), abs(ann_y - block.y_end))

            if distance < min_distance:
                min_distance = distance
                closest_block_idx = block_idx

        # Associate if within max_distance
        if closest_block_idx is not None and min_distance <= max_distance:
            mapping.associations[ann_idx] = closest_block_idx

    return mapping


def preserve_strokes_in_scene(strokes: list[Stroke], scene_blocks: list, parent_id=None) -> list:
    """Convert Stroke objects back to rmscene SceneLineItemBlock objects.

    This allows us to preserve strokes when regenerating a document by converting
    our annotation format back to the rmscene format that can be written to .rm files.

    Args:
        strokes: List of Stroke objects to preserve
        scene_blocks: Existing scene blocks to append to
        parent_id: Optional parent group ID for organization

    Returns:
        Updated scene_blocks list with new stroke blocks added

    Note:
        This function requires careful handling of CRDT IDs and block structure.
        See rmscene documentation for details on the binary format.
    """
    # TODO: Implement stroke preservation
    # This requires:
    # 1. Converting our Point objects back to rmscene Point objects
    # 2. Creating SceneLineItemBlock with proper CRDT IDs
    # 3. Adding to the scene tree structure
    # 4. Maintaining proper parent/child relationships
    raise NotImplementedError("Stroke preservation not yet implemented")


def calculate_position_mapping(
    old_blocks: list[TextBlock], new_blocks: list[TextBlock]
) -> dict[int, int]:
    """Map old text blocks to new text blocks based on content similarity.

    When regenerating a document, text blocks may have moved due to edits.
    This function tries to match old blocks to new blocks so we can reposition
    annotations appropriately.

    Uses a simple heuristic:
    1. Exact content match (best case)
    2. Fuzzy content match based on shared words
    3. Position-based fallback

    Args:
        old_blocks: Text blocks from the previous version
        new_blocks: Text blocks from the new version

    Returns:
        Dict mapping old block index → new block index

    Example:
        >>> old = [TextBlock("Hello world", 100, 150, "paragraph")]
        >>> new = [TextBlock("Hello world!", 120, 170, "paragraph")]
        >>> mapping = calculate_position_mapping(old, new)
        >>> # mapping[0] = 0 (matched by content)
    """
    mapping = {}

    # First pass: exact content matches
    for old_idx, old_block in enumerate(old_blocks):
        for new_idx, new_block in enumerate(new_blocks):
            if old_block.content.strip() == new_block.content.strip():
                mapping[old_idx] = new_idx
                break

    # Second pass: fuzzy matches for unmapped blocks
    for old_idx, old_block in enumerate(old_blocks):
        if old_idx in mapping:
            continue

        # Find best match by word overlap
        old_words = set(old_block.content.lower().split())
        best_score = 0
        best_idx = None

        for new_idx, new_block in enumerate(new_blocks):
            new_words = set(new_block.content.lower().split())
            if not old_words or not new_words:
                continue

            # Jaccard similarity
            intersection = len(old_words & new_words)
            union = len(old_words | new_words)
            score = intersection / union if union > 0 else 0

            if score > best_score and score > 0.5:  # Threshold for fuzzy match
                best_score = score
                best_idx = new_idx

        if best_idx is not None:
            mapping[old_idx] = best_idx

    return mapping


@dataclass
class TextAnchor:
    """Anchor point for an annotation in text content."""

    text_content: str
    char_offset: int | None
    context_before: str
    context_after: str
    confidence: float
    position: tuple[float, float] | None = None
    annotation_type: str | None = None


class HeuristicTextAnchor:
    """Text anchoring using substring and fuzzy matching."""

    def __init__(self, context_window: int = 50, fuzzy_threshold: float = 0.8):
        """Initialize heuristic text anchor.

        Args:
            context_window: Number of chars before/after for context
            fuzzy_threshold: Minimum similarity ratio for fuzzy match (0.0-1.0)
        """
        self.context_window = context_window
        self.fuzzy_threshold = fuzzy_threshold

    def find_anchor(
        self, annotation_text: str, old_document: str, old_position: tuple[float, float]
    ) -> TextAnchor:
        """Find annotation in old document using text + position matching.

        When multiple occurrences exist, uses BOTH X and Y position hints
        to select the correct one based on estimated position from character
        offset and word-wrapping calculation.

        Uses layout parameters from rock_paper_sync.layout.constants.
        """
        import difflib

        # Import layout constants from single source of truth
        from rock_paper_sync.layout.constants import (
            CHAR_WIDTH,
            CHARS_PER_LINE,
            LINE_HEIGHT,
            TEXT_POS_X,
            TEXT_POS_Y,
        )

        def estimate_position(offset: int) -> tuple[float, float]:
            """Estimate (x, y) position for a character offset."""
            # Count lines before this offset (explicit newlines)
            lines_before = old_document[:offset].count("\n")

            # Find start of current line
            line_start = old_document.rfind("\n", 0, offset) + 1

            # Characters from line start to offset
            chars_in_line = offset - line_start

            # Word-wrap: additional lines from characters in current paragraph
            # Find paragraph start
            para_start = old_document.rfind("\n\n", 0, offset)
            para_start = para_start + 2 if para_start != -1 else 0
            chars_in_para = offset - para_start
            wrap_lines = chars_in_para // CHARS_PER_LINE

            total_lines = lines_before + wrap_lines
            x_in_line = chars_in_line % CHARS_PER_LINE

            est_x = TEXT_POS_X + x_in_line * CHAR_WIDTH
            est_y = TEXT_POS_Y + total_lines * LINE_HEIGHT

            return (est_x, est_y)

        # Find ALL occurrences of the annotation text
        all_offsets: list[int] = []
        start = 0
        while True:
            pos = old_document.find(annotation_text, start)
            if pos == -1:
                break
            all_offsets.append(pos)
            start = pos + 1

        if all_offsets:
            # Use position hint to select best match when multiple exist
            if len(all_offsets) == 1:
                offset = all_offsets[0]
            else:
                # Calculate distance using BOTH X and Y positions
                old_x_hint, old_y_hint = old_position
                best_offset = all_offsets[0]
                best_distance = float("inf")

                for off in all_offsets:
                    est_x, est_y = estimate_position(off)
                    # Euclidean distance weighted more heavily on Y (different lines)
                    # Y is more reliable for line disambiguation
                    distance = ((est_x - old_x_hint) ** 2 + (est_y - old_y_hint) ** 2 * 4) ** 0.5

                    if distance < best_distance:
                        best_distance = distance
                        best_offset = off

                offset = best_offset

            confidence = 1.0
        else:
            # Try fuzzy match
            matcher = difflib.SequenceMatcher(None, annotation_text, old_document)
            match = matcher.find_longest_match(0, len(annotation_text), 0, len(old_document))

            if match.size >= len(annotation_text) * self.fuzzy_threshold:
                offset = match.b
                confidence = match.size / len(annotation_text)
            else:
                # No good match
                offset = None
                confidence = 0.0

        # Extract context
        if offset is not None:
            context_before = old_document[max(0, offset - self.context_window) : offset]
            context_after = old_document[
                offset + len(annotation_text) : offset + len(annotation_text) + self.context_window
            ]
        else:
            context_before = ""
            context_after = ""

        return TextAnchor(
            text_content=annotation_text,
            char_offset=offset,
            context_before=context_before,
            context_after=context_after,
            confidence=confidence,
            position=old_position,
            annotation_type="highlight",
        )

    def resolve_anchor(self, anchor: TextAnchor, new_document: str) -> int | None:
        """Resolve anchor in new document using context and position matching.

        When text is inserted/deleted, context around the anchor changes.
        We use both context matching AND position-based estimation to find
        the correct occurrence.
        """
        import difflib

        # Try exact match first
        offset = new_document.find(anchor.text_content)

        if offset != -1:
            # If multiple matches, use context AND position to disambiguate
            all_offsets = []
            start = 0
            while True:
                pos = new_document.find(anchor.text_content, start)
                if pos == -1:
                    break
                all_offsets.append(pos)
                start = pos + 1

            if len(all_offsets) == 1:
                return all_offsets[0]

            # Multiple matches - combine context and position scoring
            best_offset = all_offsets[0]
            best_score = -float("inf")

            # Estimate expected position in new doc based on old offset
            # This helps when context changes due to text insertion
            old_offset = anchor.char_offset

            for candidate_offset in all_offsets:
                # Context score (0.0 to 1.0)
                before = new_document[
                    max(0, candidate_offset - self.context_window) : candidate_offset
                ]
                after = new_document[
                    candidate_offset + len(anchor.text_content) : candidate_offset
                    + len(anchor.text_content)
                    + self.context_window
                ]

                before_score = difflib.SequenceMatcher(None, anchor.context_before, before).ratio()
                after_score = difflib.SequenceMatcher(None, anchor.context_after, after).ratio()
                context_score = (before_score + after_score) / 2

                # Position score: prefer offsets close to or after old offset
                # When text is inserted before, new offset > old offset
                # When text is deleted before, new offset < old offset
                # We expect the offset to move, so we compare relative positions
                if old_offset is not None:
                    # Calculate position-based score
                    # Offsets >= old_offset are likely correct (text inserted before)
                    # Offsets near old_offset are good
                    offset_diff = candidate_offset - old_offset
                    # Normalize: 1.0 for same position, decreasing for distance
                    # But give bonus for offsets that moved forward (text insertion)
                    if offset_diff >= 0:
                        # Offset moved forward or stayed same - likely correct
                        position_score = 1.0 / (1.0 + offset_diff / 100.0)
                    else:
                        # Offset moved backward - less likely (unless text deleted)
                        position_score = 0.5 / (1.0 + abs(offset_diff) / 100.0)
                else:
                    position_score = 0.5

                # Combine scores: weight context more if it's confident
                if context_score > 0.7:
                    # Strong context match - trust it
                    score = context_score * 0.8 + position_score * 0.2
                else:
                    # Weak context - rely more on position
                    score = context_score * 0.4 + position_score * 0.6

                if score > best_score:
                    best_score = score
                    best_offset = candidate_offset

            return best_offset

        # Fuzzy match as fallback
        matcher = difflib.SequenceMatcher(None, anchor.text_content, new_document)
        match = matcher.find_longest_match(0, len(anchor.text_content), 0, len(new_document))

        if match.size >= len(anchor.text_content) * self.fuzzy_threshold:
            return match.b

        # No match found
        return None


# Re-export WordWrapLayoutEngine from the layout module for backwards compatibility
# The canonical implementation is now in rock_paper_sync.layout.engine

__all_layout__ = ["WordWrapLayoutEngine"]
