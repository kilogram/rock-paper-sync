"""Core data types for annotation system."""

from dataclasses import dataclass


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
