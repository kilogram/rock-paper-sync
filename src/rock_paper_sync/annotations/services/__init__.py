"""Services for annotation operations.

This package provides injectable services for low-level operations:
- CrdtService: CRDT ID generation and block cloning
"""

from .crdt_service import CrdtService

__all__ = ["CrdtService"]
