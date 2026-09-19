"""Disposition sum type for ink cluster outcomes.

Every ink cluster on the device gets exactly one ``Disposition`` (D7).
Terminal dispositions (``Highlight``, ``Consumed``, ``Baked``) mean the
cluster's strokes can be cleared on the next push.  ``Deferred`` is
non-terminal: deferred strokes block the ink clearance.

Invariants relied upon:
    INV-I4: A consumed stroke never re-enters a candidate set.
    INV-I5: One stroke belongs to at most one cluster/disposition.
    INV-I7: Screen-pixel literals only in ``device_profile.py``.

Import restrictions (enforced by import-linter):
    Must NOT import ``weasyprint``, ``fitz``, ``pymupdf``,
    ``rock_paper_sync.sync_v3``, or ``rock_paper_sync.rm_cloud_sync``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "Baked",
    "Consumed",
    "Deferred",
    "Disposition",
    "Highlight",
    "InkAnchor",
    "MarkdownEdit",
    "paragraph_anchor_ref",
]


# ---------------------------------------------------------------------------
# Anchor identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InkAnchor:
    """Where a cluster is anchored in the document.

    ``kind='paragraph'``:
        ``ref`` = sha256 of normalised paragraph text + ``'#' + ordinal``
        among duplicates (0-based).  Computed from the pushed snapshot
        via the parser.
    ``kind='document_end'``:
        ``ref`` is the empty string.
    """

    kind: Literal["paragraph", "document_end"]
    ref: str


def paragraph_anchor_ref(normalised_text: str, ordinal: int) -> str:
    """Compute the ``ref`` for a paragraph anchor.

    Args:
        normalised_text: Paragraph text after whitespace normalisation.
        ordinal: 0-based index among duplicate paragraphs with the same text.

    Returns:
        ``sha256(text)#ordinal``
    """
    text_hash = hashlib.sha256(normalised_text.encode("utf-8")).hexdigest()
    return f"{text_hash}#{ordinal}"


DOCUMENT_END = InkAnchor(kind="document_end", ref="")


# ---------------------------------------------------------------------------
# Markdown edit
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarkdownEdit:
    """A single edit to be applied to the vault markdown file.

    ``kind`` determines the edit type:
    - ``'highlight'``: wrap a text span in ``==…==``.
    - ``'append'``: insert text under the ``## Notes`` heading.
    - ``'embed'``: insert ``![[name.svg]]`` after an anchor paragraph.
    - ``'deferred'``: insert an ``UNINTERPRETED`` marker block.

    For highlights, ``snapshot_span`` gives the exact ``(start, end)``
    character offset in the pushed snapshot text, derived from the word
    map's token positions.  When set, ``apply_edits`` wraps at that span
    instead of doing a substring search (which could hit the wrong
    occurrence).  ``None`` means the word map had no tokens for the
    highlight rects — ``apply_edits`` falls back to substring search.
    """

    kind: Literal["highlight", "append", "embed", "deferred"]
    cluster_id: str
    anchor: InkAnchor
    text: str  # for highlight: the matched text; for append: OCR text; for embed: wikilink
    snapshot_span: tuple[int, int] | None = None  # (start, end) in snapshot text


# ---------------------------------------------------------------------------
# Dispositions (sum type via frozen dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Highlight:
    """Cluster is a highlight over known word-map tokens.

    Terminal: strokes can be cleared on next push.
    """

    edit: MarkdownEdit


@dataclass(frozen=True, slots=True)
class Consumed:
    """Cluster was interpreted as an edit (gesture or appended text).

    Terminal: strokes are gone from the device by construction.
    """

    edit_id: str
    edit: MarkdownEdit


@dataclass(frozen=True, slots=True)
class Baked:
    """Cluster is a drawing saved as an SVG attachment.

    Terminal: the SVG is baked into the next PDF render.
    """

    attachment: str  # filename of the SVG
    anchor: InkAnchor
    svg: bytes
    edit: MarkdownEdit


@dataclass(frozen=True, slots=True)
class Deferred:
    """Cluster could not be fully processed (non-terminal).

    Deferred strokes block the ink clearance until resolved.
    """

    reason: str
    edit: MarkdownEdit


# Union type
Disposition = Highlight | Consumed | Baked | Deferred


def is_terminal(d: Disposition) -> bool:
    """Return True if the disposition is terminal (strokes can be cleared)."""
    return not isinstance(d, Deferred)
