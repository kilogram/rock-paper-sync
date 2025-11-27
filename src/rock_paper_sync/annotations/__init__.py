"""Annotation system with pluggable handlers.

This package provides a composable architecture for handling different
types of annotations (highlights, strokes, sketches, etc.) with:
- Protocol-based handlers for extensibility
- Handler-specific state management
- Generic corrections system
- Shared coordinate transformation utilities
"""

# Core annotation types and utilities
from .core_types import (
    AnnotationType,
    Point,
    Rectangle,
    Stroke,
    Highlight,
    Annotation,
    TextBlock,
    AnnotationMapping,
    TextAnchor,
    HeuristicTextAnchor,
    WordWrapLayoutEngine,
    read_annotations,
    associate_annotations_with_content,
    preserve_strokes_in_scene,
    calculate_position_mapping,
)

# Export new architecture components
from .core.data_types import AnnotationInfo
from .core.processor import AnnotationProcessor
from .core.protocol import AnnotationHandler
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
