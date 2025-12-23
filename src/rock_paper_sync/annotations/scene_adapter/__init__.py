"""Scene Graph Adapter - Bridge between domain concepts and rmscene primitives.

This package provides the abstraction layer between rock-paper-sync domain
concepts (annotations, strokes, anchors) and rmscene file format primitives
(CrdtId, TreeNodeBlock, SceneTreeBlock, etc.).

Architecture:
    Layer 1 (Domain) - Pure domain types, no rmscene imports
           |
           v
    Layer 2 (Scene Adapter) - THIS PACKAGE - translates domain <-> rmscene
           |
           v
    Layer 3 (rmscene) - External library, raw block types

Key components:
    - block_registry: Classification of rmscene blocks as KNOWN vs UNKNOWN
    - bundle: StrokeBundle - groups all 4 blocks needed for a stroke
    - scene_index: SceneGraphIndex for fast block lookups
    - translator: Converts domain annotations <-> rmscene blocks
    - executor: PageTransformExecutor - applies transformation intents

The "Preserve Unknown, Regenerate Known" strategy:
    - Unknown blocks: Sacred, preserved verbatim (protects future features)
    - Known blocks: Regenerated cleanly from source of truth (markdown + annotations)
"""

from .block_registry import (
    ANNOTATION_BLOCKS,
    REGENERATED_BLOCKS,
    SCENE_GRAPH_BLOCKS,
    BlockKind,
    classify_block,
    is_known_block,
)
from .bundle import StrokeBundle
from .executor import PageTransformExecutor
from .scene_index import (
    KNOWN_SYSTEM_NODES,
    SYSTEM_LAYER_1,
    SYSTEM_LAYER_1_GROUP,
    SYSTEM_ROOT,
    SceneGraphIndex,
    SceneGraphValidationResult,
    ValidationError,
    format_crdt_id,
    is_system_node,
    is_user_node,
    validate_scene_graph,
    validate_scene_graph_file,
)
from .translator import (
    END_OF_DOC_ANCHOR_MARKER,
    SceneTranslator,
    build_stroke_bundles,
    extract_annotation_blocks,
    extract_unknown_blocks,
    get_anchor_offset_from_tree_node,
    is_sentinel_anchor,
)

__all__ = [
    # Block registry
    "BlockKind",
    "classify_block",
    "is_known_block",
    "REGENERATED_BLOCKS",
    "ANNOTATION_BLOCKS",
    "SCENE_GRAPH_BLOCKS",
    # Bundle
    "StrokeBundle",
    # Scene index
    "SceneGraphIndex",
    "SceneGraphValidationResult",
    "ValidationError",
    "format_crdt_id",
    "is_system_node",
    "is_user_node",
    "validate_scene_graph",
    "validate_scene_graph_file",
    "KNOWN_SYSTEM_NODES",
    "SYSTEM_ROOT",
    "SYSTEM_LAYER_1",
    "SYSTEM_LAYER_1_GROUP",
    # Translator
    "SceneTranslator",
    "build_stroke_bundles",
    "extract_annotation_blocks",
    "extract_unknown_blocks",
    "get_anchor_offset_from_tree_node",
    "is_sentinel_anchor",
    "END_OF_DOC_ANCHOR_MARKER",
    # Executor
    "PageTransformExecutor",
]
