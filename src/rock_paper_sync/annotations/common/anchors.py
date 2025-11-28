"""Annotation anchoring system.

This module provides high-level abstractions for locating and matching annotations
across document versions. It encapsulates reMarkable v6 format internals (CrdtIds,
TreeNodeBlocks, coordinate transformations) behind a clean API.

Core principle: Handlers should NEVER import from rmscene or coordinate_transformer
directly. All RM v6 format complexity is hidden behind these anchor abstractions.

Design:
    AnnotationAnchor = PagePosition + TextAnchor + BoundingBox + scoring

    Handlers create anchors from annotations and use them for:
    - Matching annotations across syncs (fuzzy matching)
    - Detecting corrections (markdown changes)
    - Applying corrections (coordinate-aware updates)

Example:
    # Handler creates anchor (hides RM v6 complexity)
    anchor = AnnotationAnchor.from_highlight(
        highlight=annotation.highlight,
        page_num=0,
        text_context=paragraph_text,
        position=(100.0, 500.0)
    )

    # Later: match anchor against new document
    score = anchor.match_score(new_paragraph_text, new_position)
    if score > 0.8:
        # Found match - apply correction
        ...
"""

import difflib
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class PagePosition:
    """Physical position on a page.

    Provides page-relative positioning independent of text content.
    Useful for spatial matching when text changes significantly.

    Attributes:
        page_num: Zero-indexed page number
        x: X coordinate in page space (0-1404 for reMarkable)
        y: Y coordinate in page space (0-1872 for reMarkable)
    """

    page_num: int
    x: float
    y: float

    def distance_to(self, other: "PagePosition") -> float:
        """Calculate spatial distance to another position.

        Returns:
            Euclidean distance in page coordinates, or inf if different pages
        """
        if self.page_num != other.page_num:
            return float("inf")

        dx = self.x - other.x
        dy = self.y - other.y
        return (dx * dx + dy * dy) ** 0.5

    def similarity_score(self, other: "PagePosition", max_distance: float = 200.0) -> float:
        """Calculate position similarity score (0.0 - 1.0).

        Args:
            other: Position to compare against
            max_distance: Maximum distance for scoring (beyond this = 0.0)

        Returns:
            Similarity score: 1.0 = same position, 0.0 = far apart
        """
        distance = self.distance_to(other)
        if distance == float("inf"):
            return 0.0

        # Linear decay: distance 0 -> 1.0, distance max_distance -> 0.0
        return max(0.0, 1.0 - (distance / max_distance))


@dataclass(frozen=True)
class BoundingBox:
    """Spatial bounding box for an annotation.

    Encapsulates physical dimensions and provides geometric queries.

    Attributes:
        x: Left edge X coordinate
        y: Top edge Y coordinate
        width: Box width
        height: Box height
    """

    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        """Center X coordinate."""
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        """Center Y coordinate."""
        return self.y + self.height / 2

    @property
    def center(self) -> tuple[float, float]:
        """Center point (x, y)."""
        return (self.center_x, self.center_y)

    def overlaps(self, other: "BoundingBox") -> bool:
        """Check if this box overlaps with another."""
        return not (
            self.x + self.width < other.x
            or other.x + other.width < self.x
            or self.y + self.height < other.y
            or other.y + other.height < self.y
        )

    def overlap_area(self, other: "BoundingBox") -> float:
        """Calculate overlapping area with another box."""
        if not self.overlaps(other):
            return 0.0

        x_overlap = min(self.x + self.width, other.x + other.width) - max(self.x, other.x)
        y_overlap = min(self.y + self.height, other.y + other.height) - max(self.y, other.y)

        return x_overlap * y_overlap

    def iou(self, other: "BoundingBox") -> float:
        """Calculate Intersection over Union with another box.

        Returns:
            IoU score (0.0 - 1.0): 0.0 = no overlap, 1.0 = identical
        """
        intersection = self.overlap_area(other)
        if intersection == 0.0:
            return 0.0

        area1 = self.width * self.height
        area2 = other.width * other.height
        union = area1 + area2 - intersection

        return intersection / union if union > 0 else 0.0


@dataclass(frozen=True)
class TextAnchor:
    """Text-based anchoring with surrounding context.

    Provides robust text matching across document edits using:
    - Exact content matching
    - Surrounding context (before/after)
    - Fuzzy matching for minor edits

    Attributes:
        content: The annotation's text content (highlight text, OCR result, etc.)
        context_before: Text appearing before (up to 50 chars)
        context_after: Text appearing after (up to 50 chars)
        paragraph_index: Original paragraph index in markdown
        word_offset: Word offset within paragraph (0-based)
        char_offset: Character offset within paragraph (0-based)
    """

    content: str
    context_before: str = ""
    context_after: str = ""
    paragraph_index: int | None = None
    word_offset: int | None = None
    char_offset: int | None = None

    def match_in_text(self, text: str, fuzzy_threshold: float = 0.8) -> int | None:
        """Find this anchor's content in the given text.

        Args:
            text: Text to search in
            fuzzy_threshold: Minimum similarity for fuzzy match (0.0-1.0)

        Returns:
            Character offset of match, or None if not found
        """
        # Try exact match first
        offset = text.find(self.content)
        if offset != -1:
            return offset

        # Fuzzy match using difflib
        if not self.content:
            return None

        matcher = difflib.SequenceMatcher(None, self.content, text)
        match = matcher.find_longest_match(0, len(self.content), 0, len(text))

        similarity = match.size / len(self.content) if len(self.content) > 0 else 0.0
        if similarity >= fuzzy_threshold:
            return match.b

        return None

    def similarity_score(self, text: str, fuzzy_threshold: float = 0.8) -> float:
        """Calculate text similarity score (0.0 - 1.0).

        Args:
            text: Text to compare against
            fuzzy_threshold: Threshold for considering a match

        Returns:
            Similarity score: 1.0 = perfect match, 0.0 = no match
        """
        offset = self.match_in_text(text, fuzzy_threshold)
        if offset is None:
            return 0.0

        # Found match - calculate quality based on context
        context_score = 0.0

        # Check context before
        if self.context_before:
            actual_before = text[max(0, offset - 50) : offset]
            before_sim = difflib.SequenceMatcher(None, self.context_before, actual_before).ratio()
            context_score += before_sim * 0.5

        # Check context after
        if self.context_after:
            actual_after = text[offset + len(self.content) : offset + len(self.content) + 50]
            after_sim = difflib.SequenceMatcher(None, self.context_after, actual_after).ratio()
            context_score += after_sim * 0.5

        # Combine content match (1.0) with context match (0.0-1.0)
        # Weight: 70% content, 30% context
        return 0.7 + (context_score * 0.3)


AnnotationTypeHint = Literal["highlight", "stroke", "drawing", "note"]


@dataclass
class AnnotationAnchor:
    """Unified anchor combining all location/content information.

    Provides comprehensive annotation anchoring for:
    - Matching annotations across syncs
    - Detecting corrections from markdown edits
    - Applying corrections to RM files

    Encapsulates all RM v6 format complexity. Handlers work exclusively
    with anchors and never touch CrdtIds or coordinate transformations.

    Attributes:
        annotation_id: Content-derived stable identifier (e.g., "p3-w15-2a9f")
        annotation_type: Type of annotation (highlight, stroke, etc.)
        page: Physical page position
        bbox: Bounding box (if available)
        text: Text anchoring (if text-based)
        metadata: Type-specific metadata (confidence scores, colors, etc.)
    """

    annotation_id: str
    annotation_type: AnnotationTypeHint
    page: PagePosition
    bbox: BoundingBox | None = None
    text: TextAnchor | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_highlight(
        cls,
        highlight_text: str,
        page_num: int,
        position: tuple[float, float],
        bounding_box: tuple[float, float, float, float] | None = None,
        paragraph_index: int | None = None,
        context_before: str = "",
        context_after: str = "",
        color: int | None = None,
    ) -> "AnnotationAnchor":
        """Create anchor from highlight annotation.

        Args:
            highlight_text: Highlighted text content
            page_num: Page number
            position: (x, y) center position
            bounding_box: Optional (x, y, w, h) bounds
            paragraph_index: Paragraph index in markdown
            context_before: Text before highlight
            context_after: Text after highlight
            color: Highlight color code

        Returns:
            AnnotationAnchor for the highlight
        """
        # Create content-derived ID
        text_hash = hash(highlight_text[:30]) & 0xFFFF  # 4 hex chars
        anchor_str = f"w{paragraph_index or 0}" if paragraph_index is not None else "unk"
        annotation_id = f"p{page_num}-{anchor_str}-{text_hash:04x}"

        page = PagePosition(page_num=page_num, x=position[0], y=position[1])

        bbox_obj = None
        if bounding_box:
            bbox_obj = BoundingBox(
                x=bounding_box[0], y=bounding_box[1], width=bounding_box[2], height=bounding_box[3]
            )

        text_anchor = TextAnchor(
            content=highlight_text,
            context_before=context_before,
            context_after=context_after,
            paragraph_index=paragraph_index,
        )

        metadata = {}
        if color is not None:
            metadata["color"] = color

        return cls(
            annotation_id=annotation_id,
            annotation_type="highlight",
            page=page,
            bbox=bbox_obj,
            text=text_anchor,
            metadata=metadata,
        )

    @classmethod
    def from_stroke(
        cls,
        page_num: int,
        position: tuple[float, float],
        bounding_box: tuple[float, float, float, float],
        paragraph_index: int | None = None,
        ocr_text: str | None = None,
        context_before: str = "",
        context_after: str = "",
        image_hash: str | None = None,
        confidence: float | None = None,
    ) -> "AnnotationAnchor":
        """Create anchor from stroke annotation.

        Args:
            page_num: Page number
            position: (x, y) center position
            bounding_box: (x, y, w, h) stroke bounds
            paragraph_index: Paragraph index in markdown
            ocr_text: OCR-extracted text (if available)
            context_before: Text before stroke
            context_after: Text after stroke
            image_hash: Hash of rendered stroke image
            confidence: OCR confidence score

        Returns:
            AnnotationAnchor for the stroke
        """
        # Create content-derived ID
        pos_hash = hash((int(position[0]), int(position[1]))) & 0xFFFF
        anchor_str = f"p{paragraph_index or 0}"
        annotation_id = f"p{page_num}-{anchor_str}-{pos_hash:04x}"

        page = PagePosition(page_num=page_num, x=position[0], y=position[1])

        bbox_obj = BoundingBox(
            x=bounding_box[0], y=bounding_box[1], width=bounding_box[2], height=bounding_box[3]
        )

        text_anchor = None
        if ocr_text:
            text_anchor = TextAnchor(
                content=ocr_text,
                context_before=context_before,
                context_after=context_after,
                paragraph_index=paragraph_index,
            )

        metadata = {}
        if image_hash:
            metadata["image_hash"] = image_hash
        if confidence is not None:
            metadata["confidence"] = confidence

        return cls(
            annotation_id=annotation_id,
            annotation_type="stroke",
            page=page,
            bbox=bbox_obj,
            text=text_anchor,
            metadata=metadata,
        )

    def match_score(
        self,
        paragraph_text: str | None = None,
        position: tuple[float, float] | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> float:
        """Calculate comprehensive match score against new annotation data.

        Combines text matching, position matching, and bounding box overlap
        to produce a unified similarity score.

        Args:
            paragraph_text: New paragraph text (for text matching)
            position: New (x, y) position (for position matching)
            bbox: New (x, y, w, h) bounding box (for spatial matching)

        Returns:
            Match score (0.0 - 1.0): higher = better match
        """
        scores = []
        weights = []

        # Text matching (if available and applicable)
        if self.text and paragraph_text:
            text_score = self.text.similarity_score(paragraph_text)
            scores.append(text_score)
            weights.append(0.5)  # Text is most reliable

        # Position matching
        if position:
            new_page_pos = PagePosition(page_num=self.page.page_num, x=position[0], y=position[1])
            pos_score = self.page.similarity_score(new_page_pos)
            scores.append(pos_score)
            weights.append(0.3)

        # Bounding box matching
        if self.bbox and bbox:
            new_bbox = BoundingBox(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
            bbox_score = self.bbox.iou(new_bbox)
            scores.append(bbox_score)
            weights.append(0.2)

        # Weighted average
        if not scores:
            return 0.0

        total_weight = sum(weights)
        weighted_sum = sum(s * w for s, w in zip(scores, weights))

        return weighted_sum / total_weight
