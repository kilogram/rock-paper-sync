"""Annotation system with pluggable handlers.

This package provides a composable architecture for handling different
types of annotations (highlights, strokes, sketches, etc.) with:
- Protocol-based handlers for extensibility
- Handler-specific state management
- Generic corrections system
- Shared coordinate transformation utilities
"""

# Core annotation types and utilities
# Export new architecture components
# Re-export WordWrapLayoutEngine from layout module for backwards compatibility
from rock_paper_sync.layout import WordWrapLayoutEngine

from .core.data_types import AnnotationInfo
from .core.processor import AnnotationProcessor
from .core.protocol import AnnotationHandler
from .core_types import (
    Annotation,
    AnnotationMapping,
    AnnotationType,
    HeuristicTextAnchor,
    Highlight,
    Point,
    Rectangle,
    Stroke,
    TextAnchor,
    TextBlock,
    associate_annotations_with_content,
    calculate_position_mapping,
    preserve_strokes_in_scene,
    read_annotations,
)
from .handlers.highlight_handler import HighlightHandler
from .handlers.stroke_handler import StrokeHandler

__all__ = [
    # Core annotation types
    "AnnotationType",
    "Point",
    "Rectangle",
    "Stroke",
    "Highlight",
    "Annotation",
    "TextBlock",
    "AnnotationMapping",
    # Core utilities
    "read_annotations",
    "associate_annotations_with_content",
    "preserve_strokes_in_scene",
    "calculate_position_mapping",
    # Text layout engine
    "TextAnchor",
    "HeuristicTextAnchor",
    "WordWrapLayoutEngine",
    # Handler architecture
    "AnnotationInfo",
    "AnnotationProcessor",
    "AnnotationHandler",
    "HighlightHandler",
    "StrokeHandler",
]
