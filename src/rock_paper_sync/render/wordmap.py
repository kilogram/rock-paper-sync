"""Renderer-agnostic word-map types for the PDF substrate seam.

This module defines the frozen value types that constitute the contract
between the rendering pipeline and the ink-interpretation pipeline.
Everything downstream of ``WordMap`` (cluster classification, gesture
interpretation, OCR, markdown edits, dispositions) is backend-agnostic.

Invariants relied upon:
    INV-I1: Every parser token owns at least one laid-out box, contiguous
            in reading order (enforced by the renderer, tested per fixture).

Import restrictions (enforced by import-linter):
    This module must NOT import ``weasyprint``, ``fitz``,
    ``rock_paper_sync.sync_v3``, or ``rock_paper_sync.rm_cloud_sync``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, NewType, Protocol

if TYPE_CHECKING:
    from rock_paper_sync.device_profile import DeviceProfile
    from rock_paper_sync.parser import MarkdownDocument

__all__ = [
    "BakedOverlay",
    "BlockBox",
    "DocumentIndexEntry",
    "DocumentRenderer",
    "LineBox",
    "PageWords",
    "Placed",
    "Rect",
    "Relocated",
    "RenderedDocument",
    "TokenId",
    "TokenMatch",
    "WordMap",
]

TokenId = NewType("TokenId", int)


@dataclass(frozen=True, slots=True)
class Rect:
    """Rectangle in PDF points, top-left origin."""

    x0: float
    y0: float
    x1: float
    y1: float

    def to_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> Rect:
        return cls(x0=d["x0"], y0=d["y0"], x1=d["x1"], y1=d["y1"])


@dataclass(frozen=True, slots=True)
class BlockBox:
    """A block-level element's bounding box on a specific page.

    Published by the renderer so that ``ink/`` can resolve paragraph
    anchors by geometry alone, without fuzzy text matching.

    Attributes:
        anchor_ref: The ``data-anchor`` value (``paragraph_anchor_ref`` output).
        page: Zero-based PDF page index.
        rect: Bounding box in PDF points.
    """

    anchor_ref: str
    page: int
    rect: Rect


@dataclass(frozen=True, slots=True)
class DocumentIndexEntry:
    """Lightweight entry from a document's cloud index.

    Used by ``CloudReader.get_document_index_entries`` so callers can
    inspect entry hashes without downloading blobs.
    """

    entry_name: str
    hash: str


@dataclass(frozen=True, slots=True)
class TokenMatch:
    """A token located on a specific page."""

    token: TokenId
    page: int
    rect: Rect
    text: str
    x_fraction: tuple[float, float]


@dataclass(frozen=True, slots=True)
class LineBox:
    """A horizontal line of tokens on a page.

    Tokens are grouped into lines by y-band proximity.  Used by the
    gesture pipeline to determine line height, baseline, inter-line
    gaps, and per-line token membership.

    Attributes:
        rect: Bounding box of all tokens in the line (PDF points).
        token_ids: Token ids in reading order (left to right).
        baseline_y: Estimated baseline y coordinate.  Approximated as
            ``rect.y1`` (bottom of the token boxes).
    """

    rect: Rect
    token_ids: tuple[TokenId, ...]
    baseline_y: float


@dataclass(frozen=True, slots=True)
class PageWords:
    """All tokens on one PDF page."""

    page: int
    tokens: tuple[tuple[TokenId, Rect], ...]


@dataclass(frozen=True, slots=True)
class WordMap:
    """Maps parser tokens to their laid-out rectangles across pages.

    Invariants:
        - Every token id in ``texts`` has at least one box in some page.
        - ``rects_of`` returns a non-empty tuple; raises ``KeyError``
          on unknown id.

    Attributes:
        blocks: Block-level element boxes from the renderer's ``data-anchor``
            walk.  Each block carries the anchor ref and its laid-out rect.
        token_block: Per-token index into ``blocks`` (same length as ``texts``).
            ``-1`` for tokens not inside any anchored block.
    """

    page_size: tuple[float, float]
    pages: tuple[PageWords, ...]
    texts: tuple[str, ...]
    blocks: tuple[BlockBox, ...] = ()
    token_block: tuple[int, ...] = ()
    content_page_count: int = 0

    def token_at(self, page: int, x: float, y: float) -> TokenMatch | None:
        """Find the token whose bounding box contains the given point."""
        for pw in self.pages:
            if pw.page != page:
                continue
            for tid, rect in pw.tokens:
                if rect.x0 <= x <= rect.x1 and rect.y0 <= y <= rect.y1:
                    return TokenMatch(
                        token=tid,
                        page=page,
                        rect=rect,
                        text=self.texts[tid],
                        x_fraction=(0.0, 1.0),
                    )
        return None

    def rects_of(self, token: TokenId) -> tuple[TokenMatch, ...]:
        """Return all rects for a token, contiguous in reading order.

        Raises:
            KeyError: If token id is not found.
        """
        if token < 0 or token >= len(self.texts):
            raise KeyError(token)
        matches: list[TokenMatch] = []
        for pw in self.pages:
            for tid, rect in pw.tokens:
                if tid == token:
                    matches.append(
                        TokenMatch(
                            token=tid,
                            page=pw.page,
                            rect=rect,
                            text=self.texts[tid],
                            x_fraction=(0.0, 1.0),
                        )
                    )
        if not matches:
            raise KeyError(token)
        return tuple(matches)

    def lines_on_page(self, page: int) -> tuple[LineBox, ...]:
        """Group tokens on *page* into horizontal lines by vertical overlap.

        A token joins the current line if its vertical centre lies within
        the line's ``[y0, y1]`` band **and** its height is at most 1.5x
        the line's median token height (prevents heading-sized tokens from
        snowball-merging with body text below).

        This handles checkbox tokens whose ``y0`` differs from their item
        text by a few points: the checkbox's vertical centre still falls
        within the text line's y-band, so they share a ``LineBox``.

        Returns:
            Tuple of ``LineBox`` in top-to-bottom order.
        """
        page_tokens: list[tuple[TokenId, Rect]] = []
        for pw in self.pages:
            if pw.page == page:
                page_tokens = list(pw.tokens)
                break
        if not page_tokens:
            return ()

        sorted_toks = sorted(page_tokens, key=lambda t: (t[1].y0, t[1].x0))

        lines: list[LineBox] = []
        cur_ids: list[TokenId] = [sorted_toks[0][0]]
        cur_heights: list[float] = [sorted_toks[0][1].y1 - sorted_toks[0][1].y0]
        cur_y0 = sorted_toks[0][1].y0
        cur_y1 = sorted_toks[0][1].y1
        cur_x0 = sorted_toks[0][1].x0
        cur_x1 = sorted_toks[0][1].x1

        def _flush() -> None:
            id_to_x: dict[int, float] = {}
            for pw2 in self.pages:
                if pw2.page == page:
                    for ti, r in pw2.tokens:
                        if ti in cur_ids and int(ti) not in id_to_x:
                            id_to_x[int(ti)] = r.x0
            cur_ids_sorted = sorted(cur_ids, key=lambda t: id_to_x.get(int(t), 0.0))
            lines.append(
                LineBox(
                    rect=Rect(x0=cur_x0, y0=cur_y0, x1=cur_x1, y1=cur_y1),
                    token_ids=tuple(cur_ids_sorted),
                    baseline_y=cur_y1,
                )
            )

        for tid, rect in sorted_toks[1:]:
            tok_cy = (rect.y0 + rect.y1) / 2.0
            tok_h = rect.y1 - rect.y0

            # Vertical centre falls within the current line's y-band
            in_band = cur_y0 <= tok_cy <= cur_y1

            # Height guard: token must not be absurdly tall relative to
            # the line's existing tokens (prevents merging a heading with
            # the paragraph below it when the y-bands happen to overlap).
            sorted_h = sorted(cur_heights)
            median_h = sorted_h[len(sorted_h) // 2]
            height_ok = tok_h <= 1.5 * median_h or median_h <= 0

            if in_band and height_ok:
                cur_ids.append(tid)
                cur_heights.append(tok_h)
                cur_y0 = min(cur_y0, rect.y0)
                cur_y1 = max(cur_y1, rect.y1)
                cur_x0 = min(cur_x0, rect.x0)
                cur_x1 = max(cur_x1, rect.x1)
            else:
                _flush()
                cur_ids = [tid]
                cur_heights = [tok_h]
                cur_y0 = rect.y0
                cur_y1 = rect.y1
                cur_x0 = rect.x0
                cur_x1 = rect.x1

        _flush()
        return tuple(lines)

    def nearest_block(self, page: int, x: float, y: float) -> BlockBox | None:
        """Find the block nearest to a point on the given page.

        Uses Euclidean distance from the point to each block's centre.

        Returns:
            The nearest ``BlockBox``, or ``None`` if no blocks exist on the page.
        """
        best: BlockBox | None = None
        best_dist = float("inf")
        for blk in self.blocks:
            if blk.page != page:
                continue
            cx = (blk.rect.x0 + blk.rect.x1) / 2.0
            cy = (blk.rect.y0 + blk.rect.y1) / 2.0
            dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best = blk
        return best

    def to_json(self) -> bytes:
        """Serialise to compact JSON bytes."""
        data: dict[str, object] = {
            "page_size": list(self.page_size),
            "pages": [
                {
                    "page": pw.page,
                    "tokens": [[int(tid), r.to_dict()] for tid, r in pw.tokens],
                }
                for pw in self.pages
            ],
            "texts": list(self.texts),
        }
        if self.blocks:
            data["blocks"] = [
                {"anchor_ref": b.anchor_ref, "page": b.page, "rect": b.rect.to_dict()}
                for b in self.blocks
            ]
        if self.token_block:
            data["token_block"] = list(self.token_block)
        if self.content_page_count > 0:
            data["content_page_count"] = self.content_page_count
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> WordMap:
        """Deserialise from JSON bytes.

        Handles word maps serialised before the ``blocks`` / ``token_block``
        fields were added (they default to empty tuples).
        """
        d = json.loads(data)
        pages = tuple(
            PageWords(
                page=p["page"],
                tokens=tuple((TokenId(t[0]), Rect.from_dict(t[1])) for t in p["tokens"]),
            )
            for p in d["pages"]
        )
        blocks_raw = d.get("blocks", [])
        blocks = tuple(
            BlockBox(
                anchor_ref=b["anchor_ref"],
                page=b["page"],
                rect=Rect.from_dict(b["rect"]),
            )
            for b in blocks_raw
        )
        token_block = tuple(d.get("token_block", []))
        content_page_count = d.get("content_page_count", 0)
        return cls(
            page_size=tuple(d["page_size"]),  # type: ignore[arg-type]
            pages=pages,
            texts=tuple(d["texts"]),
            blocks=blocks,
            token_block=token_block,
            content_page_count=content_page_count,
        )


@dataclass(frozen=True, slots=True)
class BakedOverlay:
    """An SVG overlay to bake into the next PDF render (Phase 4+)."""

    anchor: str  # paragraph fingerprint or "document_end"
    offset: Rect
    svg: bytes


@dataclass(frozen=True, slots=True)
class Placed:
    """Overlay successfully placed at its anchor location."""

    overlay: BakedOverlay
    page: int
    rect: Rect


@dataclass(frozen=True, slots=True)
class Relocated:
    """Overlay whose anchor was not found; placed on the orphan page."""

    overlay: BakedOverlay
    page: int
    rect: Rect


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    """Complete render output: PDF bytes, content hash, word map.

    ``page_count`` is the total number of PDF pages (including trailing
    blank pages) as determined by the layout engine.  The payload builder
    needs this to generate ``cPages`` entries with correct ``redir`` values.
    """

    pdf: bytes
    pdf_sha256: str
    word_map: WordMap
    placements: tuple[Placed | Relocated, ...]
    page_count: int


class DocumentRenderer(Protocol):
    """Protocol for rendering a markdown document to PDF."""

    def render(
        self,
        doc: MarkdownDocument,
        profile: DeviceProfile,
        overlays: Sequence[BakedOverlay] = (),
    ) -> RenderedDocument: ...
