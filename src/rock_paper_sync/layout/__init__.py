"""Layout module for reMarkable document generation and annotation processing.

This module provides a single source of truth for layout constants and text
positioning logic used across the codebase. It consolidates:

- Page dimensions and text area constants
- Word-wrap layout engine with font metrics support
- Layout context for annotation handlers
- Coordinate transformation constants

The key abstraction is `LayoutContext`, which provides a unified interface
for all annotation handlers to access layout information without needing
to understand the underlying implementation details.

Usage:
    from rock_paper_sync.layout import (
        LayoutConstants,
        WordWrapLayoutEngine,
        LayoutContext,
    )

    # Create layout engine
    engine = WordWrapLayoutEngine()

    # Create context for annotation processing
    context = LayoutContext.from_text(text_content)
    x, y = context.offset_to_position(char_offset)
"""

from .constants import LayoutConstants
from .context import LayoutContext, TextAreaConfig
from .engine import WordWrapLayoutEngine

__all__ = [
    "LayoutConstants",
    "LayoutContext",
    "TextAreaConfig",
    "WordWrapLayoutEngine",
]
