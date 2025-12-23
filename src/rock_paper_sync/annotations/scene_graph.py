"""Scene graph utilities - DEPRECATED, use scene_adapter instead.

This module re-exports from the new scene_adapter package for backward
compatibility. New code should import directly from scene_adapter.

Migration:
    # Old (deprecated):
    from rock_paper_sync.annotations.scene_graph import StrokeBundle

    # New (preferred):
    from rock_paper_sync.annotations.scene_adapter import StrokeBundle
"""

# Re-export everything from scene_adapter for backward compatibility
from .scene_adapter import (
    KNOWN_SYSTEM_NODES,
    SYSTEM_LAYER_1,
    SYSTEM_LAYER_1_GROUP,
    SYSTEM_ROOT,
    SceneGraphIndex,
    SceneGraphValidationResult,
    StrokeBundle,
    ValidationError,
    format_crdt_id,
    is_system_node,
    is_user_node,
    validate_scene_graph,
    validate_scene_graph_file,
)

__all__ = [
    "KNOWN_SYSTEM_NODES",
    "SYSTEM_LAYER_1",
    "SYSTEM_LAYER_1_GROUP",
    "SYSTEM_ROOT",
    "SceneGraphIndex",
    "SceneGraphValidationResult",
    "StrokeBundle",
    "ValidationError",
    "format_crdt_id",
    "is_system_node",
    "is_user_node",
    "validate_scene_graph",
    "validate_scene_graph_file",
]
