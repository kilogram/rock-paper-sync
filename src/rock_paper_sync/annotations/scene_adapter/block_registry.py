"""Registry of known rmscene block types.

This module is THE authoritative source for classifying rmscene blocks.
It determines which blocks we "understand" (and can regenerate) vs which
are "unknown" (and must be preserved verbatim).

The "Preserve Unknown, Regenerate Known" strategy:
    - UNKNOWN blocks: Sacred, preserved verbatim through roundtrip
    - KNOWN blocks: We understand these and can regenerate from source of truth

This is the ONLY place that inspects rmscene block type names.
All other code should use classify_block() or is_known_block().
"""

from enum import Enum, auto
from typing import Any


class BlockKind(Enum):
    """Classification of rmscene blocks.

    Categories:
    - REGENERATED: Structural blocks we always regenerate from markdown
    - ANNOTATION: User annotation blocks we understand and can migrate
    - SCENE_GRAPH: Scene graph structure blocks we understand
    - HEADER: Document metadata blocks
    - UNKNOWN: Anything else - sacred, preserved verbatim
    """

    # Header/metadata blocks - regenerated
    AUTHOR_IDS = auto()
    MIGRATION_INFO = auto()
    PAGE_INFO = auto()

    # Text content - regenerated from markdown
    ROOT_TEXT = auto()

    # Scene graph structure - we understand these
    SCENE_TREE = auto()
    TREE_NODE = auto()
    SCENE_GROUP_ITEM = auto()

    # Annotation blocks - we understand and migrate
    STROKE = auto()  # SceneLineItemBlock
    HIGHLIGHT = auto()  # SceneGlyphItemBlock

    # Unknown - SACRED, preserved verbatim
    UNKNOWN = auto()


def classify_block(block: Any) -> BlockKind:
    """Classify an rmscene block by its type.

    This is the ONLY function that inspects block type names.
    All classification logic is centralized here.

    Args:
        block: An rmscene block object

    Returns:
        BlockKind indicating the block's classification
    """
    type_name = type(block).__name__

    # Header/metadata blocks
    if type_name == "AuthorIdsBlock":
        return BlockKind.AUTHOR_IDS
    if type_name == "MigrationInfoBlock":
        return BlockKind.MIGRATION_INFO
    if type_name == "PageInfoBlock":
        return BlockKind.PAGE_INFO

    # Text content
    if type_name == "RootTextBlock":
        return BlockKind.ROOT_TEXT

    # Scene graph structure
    if type_name == "SceneTreeBlock":
        return BlockKind.SCENE_TREE
    if type_name == "TreeNodeBlock":
        return BlockKind.TREE_NODE
    if type_name == "SceneGroupItemBlock":
        return BlockKind.SCENE_GROUP_ITEM

    # Annotation blocks - use substring matching for robustness
    # (handles SceneLineItemBlock variants)
    if "Line" in type_name:
        return BlockKind.STROKE
    if "Glyph" in type_name:
        return BlockKind.HIGHLIGHT

    # Everything else is UNKNOWN - preserved verbatim
    return BlockKind.UNKNOWN


def is_known_block(block: Any) -> bool:
    """Check if a block is a type we understand.

    Known blocks can be regenerated or migrated.
    Unknown blocks must be preserved verbatim.
    """
    return classify_block(block) != BlockKind.UNKNOWN


# Block categories for different operations

# Blocks we always regenerate from markdown source of truth
REGENERATED_BLOCKS = frozenset(
    {
        BlockKind.AUTHOR_IDS,
        BlockKind.MIGRATION_INFO,
        BlockKind.PAGE_INFO,
        BlockKind.ROOT_TEXT,
    }
)

# Blocks that carry user annotation data (we migrate these)
ANNOTATION_BLOCKS = frozenset(
    {
        BlockKind.STROKE,
        BlockKind.HIGHLIGHT,
    }
)

# Blocks that form the scene graph structure
SCENE_GRAPH_BLOCKS = frozenset(
    {
        BlockKind.SCENE_TREE,
        BlockKind.TREE_NODE,
        BlockKind.SCENE_GROUP_ITEM,
    }
)
