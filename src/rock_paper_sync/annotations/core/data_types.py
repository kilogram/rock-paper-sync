"""Core data types for annotation system."""

from dataclasses import dataclass
from typing import Literal


@dataclass
class RenderConfig:
    """Configuration for how annotations are rendered in markdown.

    Attributes:
        highlight_style: How to render highlights ("mark", "bold", "italic")
        stroke_style: How to render strokes/OCR ("footnote", "comment")
    """

    highlight_style: Literal["mark", "bold", "italic"] = "mark"
    stroke_style: Literal["footnote", "comment"] = "comment"


@dataclass
class ExtractedAnnotation:
    """Annotation extracted from markdown.

    Attributes:
        text: Extracted text content
        annotation_type: Type of annotation ("highlight", "stroke", etc.)
        start_offset: Character offset in paragraph where annotation starts
        end_offset: Character offset in paragraph where annotation ends
    """

    text: str
    annotation_type: str
    start_offset: int = -1
    end_offset: int = -1


@dataclass
class AnnotationInfo:
    """Summary of annotations for a single paragraph.

    Attributes:
        highlights: Count of highlight annotations
        strokes: Count of hand-drawn stroke annotations
        notes: Count of text note annotations (future)
    """

    highlights: int = 0
    strokes: int = 0
    notes: int = 0

    @property
    def total(self) -> int:
        """Total number of annotations."""
        return self.highlights + self.strokes + self.notes

    def __str__(self) -> str:
        """Human-readable summary for markers."""
        parts = []
        if self.highlights:
            parts.append(f"{self.highlights} highlight{'s' if self.highlights != 1 else ''}")
        if self.strokes:
            parts.append(f"{self.strokes} stroke{'s' if self.strokes != 1 else ''}")
        if self.notes:
            parts.append(f"{self.notes} note{'s' if self.notes != 1 else ''}")
        return ", ".join(parts) if parts else "0 annotations"


@dataclass
class OCRCorrection:
    """OCR correction for training data.

    Simple dataclass for collecting OCR corrections when users fix OCR
    errors in markdown. Used exclusively for training data collection,
    not for bidirectional sync.

    Attributes:
        image_hash: Hash of the annotation image (for training dataset)
        original_text: OCR text before user correction
        corrected_text: Text after user correction
        paragraph_context: Full paragraph text for context
        document_id: Document identifier (vault_name/file_path)
        annotation_id: Annotation UUID
    """

    image_hash: str
    original_text: str
    corrected_text: str
    paragraph_context: str
    document_id: str
    annotation_id: str
