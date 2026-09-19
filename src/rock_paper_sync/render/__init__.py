"""PDF rendering pipeline for reMarkable documents.

Converts markdown AST to PDF via HTML+CSS (WeasyPrint).  The ``wordmap``
module defines the renderer-agnostic seam types; ``html`` converts the
parser AST to tokenised HTML; ``pdf`` drives the layout engine and builds
the WordMap from the box tree.
"""

__all__: list[str] = []
