"""
reMarkable-Obsidian Sync Tool

Sync Obsidian markdown files to reMarkable Paper Pro documents.
"""

import logging

__version__ = "0.1.0"
__author__ = "Your Name"

# Suppress known noisy warnings from third-party libraries
# rmscene warning about text format code 10 (newline character - see docs/RMSCENE_NEWLINE_WORKAROUND.md)
logging.getLogger("rmscene.scene_stream").addFilter(
    lambda record: "Unrecognised text format code 10" not in record.getMessage()
)

# Only import modules that exist
from .parser import MarkdownDocument, parse_markdown_file

__all__ = [
    "MarkdownDocument",
    "parse_markdown_file",
]
