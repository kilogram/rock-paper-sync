"""rmlib - Library for rendering reMarkable .rm files to PNG.

This package provides a custom renderer for .rm files that is validated
against device thumbnails, replacing the unreliable rmc tool.
"""

from .renderer import RmRenderer

__all__ = ["RmRenderer"]
