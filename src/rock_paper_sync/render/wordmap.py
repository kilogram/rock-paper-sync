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
    "DocumentRenderer",
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
class TokenMatch:
    """A token located on a specific page."""

    token: TokenId
    page: int
    rect: Rect
    text: str
    x_fraction: tuple[float, float]


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
    """

    page_size: tuple[float, float]
    pages: tuple[PageWords, ...]
    texts: tuple[str, ...]

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

    def to_json(self) -> bytes:
        """Serialise to compact JSON bytes."""
        data = {
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
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> WordMap:
        """Deserialise from JSON bytes."""
        d = json.loads(data)
        pages = tuple(
            PageWords(
                page=p["page"],
                tokens=tuple((TokenId(t[0]), Rect.from_dict(t[1])) for t in p["tokens"]),
            )
            for p in d["pages"]
        )
        return cls(
            page_size=tuple(d["page_size"]),  # type: ignore[arg-type]
            pages=pages,
            texts=tuple(d["texts"]),
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
    """Complete render output: PDF bytes, content hash, word map."""

    pdf: bytes
    pdf_sha256: str
    word_map: WordMap
    placements: tuple[Placed | Relocated, ...]


class DocumentRenderer(Protocol):
    """Protocol for rendering a markdown document to PDF."""

    def render(
        self,
        doc: MarkdownDocument,
        profile: DeviceProfile,
        overlays: Sequence[BakedOverlay] = (),
    ) -> RenderedDocument: ...
