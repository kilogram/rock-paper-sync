"""Domain layer for annotations - pure types with no rmscene imports.

This package contains the domain model for annotations, which represents
"what we want to do" without knowing "how rmscene does it".

Key types:
- TransformIntent: Base class for all transformation intents
- StrokePlacement: Intent to place a stroke at a specific anchor
- HighlightPlacement: Intent to place a highlight at a text span
- PageTransformPlan: Complete transformation plan for a page

The domain layer communicates with the scene_adapter layer through intents.
This creates a clean separation where:
- Domain code expresses WHAT should happen (place stroke at anchor X)
- Scene adapter handles HOW it happens (create TreeNodeBlock, etc.)
"""

from .intents import (
    HighlightPlacement,
    PageTransformPlan,
    PreserveUnknown,
    StrokePlacement,
    TransformIntent,
)

__all__ = [
    "TransformIntent",
    "StrokePlacement",
    "HighlightPlacement",
    "PreserveUnknown",
    "PageTransformPlan",
]
