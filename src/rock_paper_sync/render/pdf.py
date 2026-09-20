"""WeasyPrint PDF renderer implementing ``DocumentRenderer``.

Renders tokenised HTML (from ``render.html``) to PDF using a vendored
OFL font.  Builds the ``WordMap`` from WeasyPrint's laid-out box tree
by walking each page's descendants and collecting boxes whose element
carries a ``data-token`` attribute.

Invariants established:
    INV-I1: Every parser token has at least one laid-out box.  Asserted
            after the box-tree walk; raises ``TokenBoxError`` on violation.

Import note:
    This module imports ``weasyprint`` at the top level.  It is an
    adapter-layer module and is not imported by ``render.wordmap`` or
    ``cloud_reader``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import weasyprint

from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.device_profile import DeviceProfile
from rock_paper_sync.parser import MarkdownDocument
from rock_paper_sync.render.html import ANCHOR_ATTR, TOKEN_ATTR, ast_to_html
from rock_paper_sync.render.wordmap import (
    BakedOverlay,
    BlockBox,
    PageWords,
    Placed,
    Rect,
    Relocated,
    RenderedDocument,
    TokenId,
    WordMap,
)

__all__ = ["PdfRenderer", "TokenBoxError"]

logger = logging.getLogger(__name__)

# Path to vendored fonts
_VENDOR_FONTS_DIR = Path(__file__).parent.parent / "fonts" / "vendor"

# CSS px to PDF pt conversion factor
_CSS_PX_TO_PT = 0.75


class TokenBoxError(Exception):
    """Raised when a token has zero laid-out boxes (I1 violation)."""

    def __init__(self, token_id: int, token_text: str) -> None:
        self.token_id = token_id
        self.token_text = token_text
        super().__init__(
            f"Token {token_id} ({token_text!r}) has no laid-out boxes. "
            f"This violates I1: every token must own at least one box."
        )


def _build_css(
    layout: LayoutConfig,
    profile: DeviceProfile,
) -> str:
    """Build the CSS stylesheet for the PDF render."""
    page_w = profile.page_width_pt
    page_h = profile.page_height_pt
    font_size = layout.font_size_pt
    line_height = layout.line_height
    mt = layout.margin_top_pt
    mb = layout.margin_bottom_pt
    ml = layout.margin_left_pt
    mr = layout.margin_right_pt

    # Line spacing for ruled blank pages
    line_spacing_pt = font_size * line_height

    return f"""
@page {{
    size: {page_w}pt {page_h}pt;
    margin: {mt}pt {mr}pt {mb}pt {ml}pt;
}}

@font-face {{
    font-family: 'RPS Body';
    src: url('file://{_VENDOR_FONTS_DIR / "NotoSans-Regular.ttf"}');
    font-weight: normal;
    font-style: normal;
}}
@font-face {{
    font-family: 'RPS Body';
    src: url('file://{_VENDOR_FONTS_DIR / "NotoSans-Bold.ttf"}');
    font-weight: bold;
    font-style: normal;
}}
@font-face {{
    font-family: 'RPS Body';
    src: url('file://{_VENDOR_FONTS_DIR / "NotoSans-Italic.ttf"}');
    font-weight: normal;
    font-style: italic;
}}
@font-face {{
    font-family: 'RPS Body';
    src: url('file://{_VENDOR_FONTS_DIR / "NotoSans-BoldItalic.ttf"}');
    font-weight: bold;
    font-style: italic;
}}
@font-face {{
    font-family: 'RPS Mono';
    src: url('file://{_VENDOR_FONTS_DIR / "NotoSansMono-Regular.ttf"}');
    font-weight: normal;
    font-style: normal;
}}

body {{
    font-family: 'RPS Body', sans-serif;
    font-size: {font_size}pt;
    line-height: {line_height};
    color: #000;
    overflow-wrap: anywhere;
}}

h1 {{ font-size: {font_size * layout.h1_scale}pt; margin: 0.5em 0 0.3em 0; }}
h2 {{ font-size: {font_size * layout.h2_scale}pt; margin: 0.5em 0 0.3em 0; }}
h3 {{ font-size: {font_size * layout.h3_scale}pt; margin: 0.4em 0 0.2em 0; }}
h4, h5, h6 {{ font-size: {font_size * layout.h4_scale}pt; font-weight: bold; margin: 0.4em 0 0.2em 0; }}

p {{ margin: 0.4em 0; }}

mark {{
    background-color: #fff3b0;
    padding: 0 1pt;
}}

.rps-list-item {{
    margin: 0.15em 0;
    padding-left: 1.5em;
    text-indent: -1.2em;
}}
.rps-indent-2 {{ padding-left: 3.0em; }}
.rps-indent-3 {{ padding-left: 4.5em; }}
.rps-indent-4 {{ padding-left: 6.0em; }}

.rps-marker {{
    display: inline;
    min-width: 1em;
}}

del {{
    text-decoration: line-through;
}}

.rps-link {{
    text-decoration: underline;
}}

.rps-checkbox {{
    display: inline-block;
    width: {font_size}pt;
    height: {font_size}pt;
    border: 1.5pt solid #000;
    vertical-align: middle;
    text-align: center;
    font-weight: bold;
    font-size: {font_size * 0.9}pt;
    line-height: {font_size}pt;
    margin-right: 4pt;
    color: transparent;
}}
.rps-checkbox-checked {{
    color: #000;
}}

.rps-task-item {{
    padding-left: 0;
    text-indent: 0;
}}

pre.rps-code {{
    font-family: 'RPS Mono', monospace;
    font-size: {font_size * 0.85}pt;
    background-color: #f5f5f5;
    padding: 0.5em;
    border: 0.5pt solid #ccc;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}}

blockquote {{
    border-left: 2pt solid #999;
    padding-left: 0.8em;
    margin-left: 0;
    color: #444;
    font-style: italic;
}}

hr {{
    border: none;
    border-top: 1pt solid #666;
    margin: 0.8em 0;
}}

table.rps-table {{
    border-collapse: collapse;
    margin: 0.5em 0;
    font-size: {font_size * 0.9}pt;
    width: 100%;
}}
table.rps-table th, table.rps-table td {{
    border: 0.5pt solid #999;
    padding: 3pt 5pt;
    text-align: left;
}}
table.rps-table th {{
    font-weight: bold;
    background-color: #f0f0f0;
}}

sup {{
    font-size: 0.7em;
    vertical-align: super;
    line-height: 0;
}}

/* Overlay positioning: anchored block gets position:relative so the
   absolutely-positioned overlay <img> renders at the correct offset. */
.rps-has-overlay {{
    position: relative;
}}
.rps-overlay-img {{
    position: absolute;
}}
/* document_end overlays container */
.rps-doc-end-overlays {{
    position: relative;
}}
/* Orphaned ink page */
.rps-orphan-page {{
    page-break-before: always;
    position: relative;
}}

.rps-blank-page {{
    page-break-before: always;
    height: {page_h - mt - mb}pt;
    background-image: repeating-linear-gradient(
        to bottom,
        transparent,
        transparent {line_spacing_pt - 0.5}pt,
        #e0e0e0 {line_spacing_pt - 0.5}pt,
        #e0e0e0 {line_spacing_pt}pt
    );
}}
"""


_OVERLAY_ATTR = "data-overlay"


def _walk_box_tree(
    page_box: object,
    token_boxes: dict[int, list[tuple[int, float, float, float, float]]],
    page_idx: int,
    token_anchors: dict[int, str] | None = None,
    overlay_boxes: dict[int, tuple[int, float, float, float, float]] | None = None,
) -> None:
    """Walk a WeasyPrint page box tree and collect token positions.

    Walks recursively, propagating the nearest ancestor's ``data-token``
    value downward so that text boxes nested inside formatting elements
    (``<strong>``, ``<em>``, etc.) inherit the token id from their
    ancestor ``<span data-token="N">``.

    Also collects ``data-anchor`` inheritance per token (when
    *token_anchors* is provided) and ``data-overlay`` boxes (when
    *overlay_boxes* is provided).

    Args:
        page_box: WeasyPrint page box (from ``document.pages[i]._page_box``).
        token_boxes: Dict mapping token_id -> list of (page, x, y, w, h) in CSS px.
        page_idx: Zero-based page index.
        token_anchors: If provided, records the ``data-anchor`` ref each
            token is inside (first occurrence wins).
        overlay_boxes: If provided, records data-overlay index -> (page, x, y, w, h)
            in CSS px for each overlay ``<img>`` element.
    """

    def _recurse(box: object, inherited_token: int | None, inherited_anchor: str | None) -> None:
        element = getattr(box, "element", None)
        current_token = inherited_token
        current_anchor = inherited_anchor

        if element is not None and hasattr(element, "get"):
            # data-token
            token_val = element.get(TOKEN_ATTR)
            if token_val is not None:
                try:
                    current_token = int(token_val)
                except (ValueError, TypeError):
                    pass

            # data-anchor
            anchor_val = element.get(ANCHOR_ATTR)
            if anchor_val is not None:
                current_anchor = anchor_val

            # data-overlay: record the box position for overlay placement
            if overlay_boxes is not None:
                overlay_val = element.get(_OVERLAY_ATTR)
                if overlay_val is not None:
                    try:
                        ov_idx = int(overlay_val)
                    except (ValueError, TypeError):
                        pass
                    else:
                        pos_x = getattr(box, "position_x", None)
                        pos_y = getattr(box, "position_y", None)
                        width = getattr(box, "width", None)
                        height = getattr(box, "height", None)
                        if pos_x is not None and pos_y is not None and width and height:
                            overlay_boxes[ov_idx] = (
                                page_idx,
                                float(pos_x),
                                float(pos_y),
                                float(width),
                                float(height),
                            )

        # Collect leaf boxes (text, replaced, atomic inline, or inline-block)
        box_type_name = type(box).__name__
        is_leaf = box_type_name in (
            "TextBox",
            "InlineReplacedBox",
            "AtomicInlineLevelBox",
        )
        # InlineBlockBox is treated as a leaf if it carries or inherits a token
        # (e.g. checkbox spans).  Don't recurse into it.
        is_inline_block = box_type_name == "InlineBlockBox"

        if (is_leaf or is_inline_block) and current_token is not None:
            pos_x = getattr(box, "position_x", None)
            pos_y = getattr(box, "position_y", None)
            width = getattr(box, "width", None)
            height = getattr(box, "height", None)

            if (
                pos_x is not None
                and pos_y is not None
                and width is not None
                and height is not None
                and width > 0
                and height > 0
            ):
                token_boxes[current_token].append((page_idx, pos_x, pos_y, width, height))
                # Record token -> anchor mapping (first occurrence)
                if (
                    token_anchors is not None
                    and current_anchor is not None
                    and current_token not in token_anchors
                ):
                    token_anchors[current_token] = current_anchor

            if is_inline_block:
                return  # Don't recurse into inline-block children

        # Recurse into children
        for child in getattr(box, "children", []):
            _recurse(child, current_token, current_anchor)

    _recurse(page_box, None, None)


def _boxes_to_wordmap(
    token_boxes: dict[int, list[tuple[int, float, float, float, float]]],
    token_texts: list[str],
    page_size_pt: tuple[float, float],
    anchor_boxes: dict[str, tuple[int, float, float, float, float]] | None = None,
    token_anchors: dict[int, str] | None = None,
) -> WordMap:
    """Convert collected boxes into a WordMap.

    CSS px are converted to PDF points (* 0.75).

    Args:
        token_boxes: Dict mapping token_id -> list of (page, x, y, w, h) in CSS px.
        token_texts: Token texts in reading order.
        page_size_pt: Page size (width, height) in PDF points.
        anchor_boxes: Mapping of anchor_ref -> (page, x, y, w, h) in PDF pts,
            from ``_walk_anchor_boxes``.  Used to build ``blocks``.
        token_anchors: Mapping of token_id -> anchor_ref, from the extended
            box-tree walk.  Used to build ``token_block``.

    Raises:
        TokenBoxError: If any token has zero boxes (I1 violation).
    """
    # Group by page
    page_tokens: dict[int, list[tuple[TokenId, Rect]]] = defaultdict(list)

    for tid_int in range(len(token_texts)):
        tid = TokenId(tid_int)
        boxes = token_boxes.get(tid_int, [])

        if not boxes:
            raise TokenBoxError(tid_int, token_texts[tid_int])

        # Sort by (page, y, x) for reading order
        boxes.sort(key=lambda b: (b[0], b[1], b[2]))

        # Deduplicate: a <sup> or line-height:0 element can produce
        # multiple leaf boxes for the same token on the same page
        # (one for the base text and one for the superscript).
        # For overflow-wrap, a token legitimately produces multiple
        # boxes at non-adjacent x positions (I1).
        #
        # Strategy: merge boxes that are adjacent or overlapping
        # (x-end of one within 2px of x-start of next, AND y-ranges
        # overlap) into a single union box.  Boxes separated by a
        # horizontal gap > 2px are overflow-wrap and kept separate.
        by_page: dict[int, list[tuple[float, float, float, float]]] = {}
        for page_idx, px_x, px_y, px_w, px_h in boxes:
            by_page.setdefault(page_idx, []).append((px_x, px_y, px_w, px_h))

        deduped: list[tuple[int, float, float, float, float]] = []
        for page_idx, page_boxes in by_page.items():
            if len(page_boxes) <= 1:
                for bx, by, bw, bh in page_boxes:
                    deduped.append((page_idx, bx, by, bw, bh))
                continue

            # Sort by x position, then merge adjacent boxes
            sorted_boxes = sorted(page_boxes, key=lambda b: b[0])
            merged: list[list[float]] = []  # [x0, y0, x1, y1]
            for bx, by, bw, bh in sorted_boxes:
                bx1 = bx + bw
                by1 = by + bh
                if merged:
                    prev = merged[-1]
                    # Adjacent: prev x1 within 2px of current x0,
                    # and y ranges overlap
                    y_overlap = prev[1] < by1 and by < prev[3]
                    x_adjacent = bx - prev[2] < 2.0
                    if x_adjacent and y_overlap:
                        # Merge: union of the two
                        prev[0] = min(prev[0], bx)
                        prev[1] = min(prev[1], by)
                        prev[2] = max(prev[2], bx1)
                        prev[3] = max(prev[3], by1)
                        continue
                merged.append([bx, by, bx1, by1])

            for x0, y0, x1, y1 in merged:
                deduped.append((page_idx, x0, y0, x1 - x0, y1 - y0))

        for page_idx, px_x, px_y, px_w, px_h in deduped:
            # Convert CSS px to PDF pt
            x0 = px_x * _CSS_PX_TO_PT
            y0 = px_y * _CSS_PX_TO_PT
            x1 = (px_x + px_w) * _CSS_PX_TO_PT
            y1 = (px_y + px_h) * _CSS_PX_TO_PT
            rect = Rect(x0=x0, y0=y0, x1=x1, y1=y1)
            page_tokens[page_idx].append((tid, rect))

    # Build pages in order
    max_page = max(page_tokens.keys()) if page_tokens else 0
    pages: list[PageWords] = []
    for p in range(max_page + 1):
        toks = tuple(page_tokens.get(p, []))
        pages.append(PageWords(page=p, tokens=toks))

    # Build blocks from anchor_boxes (already in PDF pts)
    blocks: list[BlockBox] = []
    anchor_ref_to_block_idx: dict[str, int] = {}
    if anchor_boxes:
        for ref, (pg, ax, ay, aw, ah) in anchor_boxes.items():
            idx = len(blocks)
            anchor_ref_to_block_idx[ref] = idx
            blocks.append(
                BlockBox(
                    anchor_ref=ref,
                    page=pg,
                    rect=Rect(x0=ax, y0=ay, x1=ax + aw, y1=ay + ah),
                )
            )

    # Build token_block mapping
    tb: list[int] = []
    if token_anchors:
        for tid_int in range(len(token_texts)):
            anchor = token_anchors.get(tid_int)
            if anchor is not None and anchor in anchor_ref_to_block_idx:
                tb.append(anchor_ref_to_block_idx[anchor])
            else:
                tb.append(-1)

    return WordMap(
        page_size=page_size_pt,
        pages=tuple(pages),
        texts=tuple(token_texts),
        blocks=tuple(blocks),
        token_block=tuple(tb),
    )


class PdfRenderer:
    """Renders markdown documents to PDF via WeasyPrint.

    Implements the ``DocumentRenderer`` protocol.

    Invariants established:
        INV-I1: Every parser token has >= 1 box (asserted, raises TokenBoxError).

    Args:
        layout: Layout configuration (font size, margins, etc.).
    """

    def __init__(self, layout: LayoutConfig) -> None:
        self._layout = layout
        self._font_config = weasyprint.text.ffi.ffi  # noqa: F841 — keep reference
        # Build font config that only uses our vendored fonts
        self._wp_font_config = weasyprint.text.fonts.FontConfiguration()

    def render(
        self,
        doc: MarkdownDocument,
        profile: DeviceProfile,
        overlays: Sequence[BakedOverlay] = (),
    ) -> RenderedDocument:
        """Render a markdown document to PDF.

        Overlay positioning (I11): each overlay ``<img>`` is injected
        *inside* its anchor block element (``position: absolute`` within
        ``position: relative``).  ``document_end`` overlays go in a
        container after the last body block.  Unresolved anchors land on
        an appended "Orphaned ink" page (``Relocated``).

        ``Placed.rect`` is read back from the laid-out box tree via
        ``data-overlay="{index}"`` so it reflects where the pixels are.

        Args:
            doc: Parsed markdown document.
            profile: Target device profile for page geometry.
            overlays: Baked overlays to place (Phase 4+).

        Returns:
            Complete rendered document with PDF bytes, hash, and word map.

        Raises:
            TokenBoxError: If a token has zero boxes (I1 violation).
        """
        # Build tokenised HTML
        body_html, total_tokens = ast_to_html(
            doc.content,
            trailing_blank_pages=self._layout.trailing_blank_pages,
            page_height_pt=profile.page_height_pt,
        )

        # Inject overlays into the HTML (inside anchor elements)
        overlay_info: list[_OverlayInfo] = []
        if overlays:
            body_html, overlay_info = _inject_overlays(body_html, overlays)

        # Build CSS
        css_str = _build_css(self._layout, profile)

        # Full HTML document
        full_html = (
            "<!DOCTYPE html>\n<html>\n"
            f'<head><meta charset="utf-8"><style>{css_str}</style></head>\n'
            f"<body>{body_html}</body>\n</html>"
        )

        # Render with WeasyPrint
        html_doc = weasyprint.HTML(string=full_html)
        wp_doc = html_doc.render(font_config=self._wp_font_config)

        # Walk the box tree to build token positions, anchor boxes,
        # token→anchor mapping, and overlay box positions.
        token_boxes: dict[int, list[tuple[int, float, float, float, float]]] = defaultdict(list)
        token_texts = _collect_token_texts(doc, self._layout.trailing_blank_pages)
        anchor_boxes: dict[str, tuple[int, float, float, float, float]] = {}
        token_anchors: dict[int, str] = {}
        ov_boxes: dict[int, tuple[int, float, float, float, float]] = {}

        for page_idx, page in enumerate(wp_doc.pages):
            _walk_box_tree(
                page._page_box,
                token_boxes,
                page_idx,
                token_anchors=token_anchors,
                overlay_boxes=ov_boxes,
            )
            _walk_anchor_boxes(page._page_box, anchor_boxes, page_idx)

        # Build word map with blocks and token_block (asserts I1)
        word_map = _boxes_to_wordmap(
            token_boxes,
            token_texts,
            profile.page_size_pt,
            anchor_boxes=anchor_boxes,
            token_anchors=token_anchors,
        )

        # Build placements from the actual laid-out overlay box positions
        placements = _build_placements(overlays, overlay_info, ov_boxes, len(wp_doc.pages))

        # Write PDF bytes
        pdf_bytes = wp_doc.write_pdf()

        # Compute SHA-256
        pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

        logger.info(
            f"Rendered {len(wp_doc.pages)} page(s), {total_tokens} token(s), "
            f"{len(pdf_bytes)} bytes, {len(placements)} overlay(s)"
        )

        return RenderedDocument(
            pdf=pdf_bytes,
            pdf_sha256=pdf_sha256,
            word_map=word_map,
            placements=placements,
            page_count=len(wp_doc.pages),
        )


def _collect_token_texts(
    doc: MarkdownDocument,
    trailing_blank_pages: int,
) -> list[str]:
    """Collect the token texts in reading order, matching what ast_to_html produces.

    This must produce the exact same sequence as ast_to_html's token counter.
    """
    from rock_paper_sync.parser import BlockType
    from rock_paper_sync.render.html import (
        _CHECKBOX_CHECKED,
        _CHECKBOX_UNCHECKED,
        _ORDERED_PREFIX_RE,
        _strip_rps_markers,
        _tokenise_text,
    )

    texts: list[str] = []

    for block in doc.content:
        text = _strip_rps_markers(block.text) if block.text else ""
        if not text and block.type not in (BlockType.HORIZONTAL_RULE, BlockType.TABLE):
            continue

        if block.type == BlockType.LIST_ITEM:
            is_task = text.startswith(_CHECKBOX_CHECKED) or text.startswith(_CHECKBOX_UNCHECKED)

            if is_task:
                # Task items: checkbox is the marker (no bullet token).
                remaining = text
                if text.startswith(_CHECKBOX_CHECKED):
                    texts.append("[x]")
                    remaining = text[len(_CHECKBOX_CHECKED) :]
                else:
                    texts.append("[ ]")
                    remaining = text[len(_CHECKBOX_UNCHECKED) :]
            else:
                # Detect ordered-number prefix or use bullet as marker
                ordered_match = _ORDERED_PREFIX_RE.match(text)
                if ordered_match:
                    texts.append(f"{ordered_match.group(1)}.")
                    remaining = text[ordered_match.end() :]
                else:
                    texts.append("•")
                    remaining = text

            # Word tokens
            texts.extend(_tokenise_text(remaining))

        elif block.type == BlockType.CODE_BLOCK:
            # Each non-empty line is a token
            for line in text.split("\n"):
                if line.strip():
                    texts.append(line)

        elif block.type == BlockType.TABLE:
            # Table cells: each cell's text is tokenised
            for row in block.children:
                for cell in row.children:
                    if cell.text:
                        texts.extend(_tokenise_text(cell.text))

        elif block.type == BlockType.HORIZONTAL_RULE:
            pass  # no tokens

        else:
            # Headers, paragraphs, blockquotes: word tokens
            texts.extend(_tokenise_text(text))

    return texts


def _walk_anchor_boxes(
    page_box: object,
    anchor_boxes: dict[str, tuple[int, float, float, float, float]],
    page_idx: int,
) -> None:
    """Walk a WeasyPrint page box tree and collect anchor block positions.

    Finds elements with ``data-anchor`` attributes and records their
    bounding box (page, x, y, w, h) in PDF pts.  Only the first
    occurrence of each anchor ref is recorded (stable across pages).

    Args:
        page_box: WeasyPrint page box.
        anchor_boxes: Dict mapping anchor_ref -> (page, x, y, w, h) in PDF pts.
        page_idx: Zero-based page index.
    """

    def _recurse(box: object) -> None:
        element = getattr(box, "element", None)
        if element is not None and hasattr(element, "get"):
            anchor_val = element.get(ANCHOR_ATTR)
            if anchor_val is not None and anchor_val not in anchor_boxes:
                pos_x = getattr(box, "position_x", None)
                pos_y = getattr(box, "position_y", None)
                width = getattr(box, "width", None)
                height = getattr(box, "height", None)
                if pos_x is not None and pos_y is not None and width and height:
                    anchor_boxes[anchor_val] = (
                        page_idx,
                        float(pos_x) * _CSS_PX_TO_PT,
                        float(pos_y) * _CSS_PX_TO_PT,
                        float(width) * _CSS_PX_TO_PT,
                        float(height) * _CSS_PX_TO_PT,
                    )

        children = getattr(box, "children", None)
        if children:
            for child in children:
                _recurse(child)

    _recurse(page_box)


# ---------------------------------------------------------------------------
# Overlay injection and placement
# ---------------------------------------------------------------------------


@dataclass
class _OverlayInfo:
    """Bookkeeping for one overlay during injection."""

    index: int
    kind: str  # "placed", "document_end", "relocated"


def _overlay_img_tag(index: int, overlay: BakedOverlay) -> str:
    """Build an ``<img>`` tag for an overlay with absolute positioning."""
    b64 = base64.b64encode(overlay.svg).decode("ascii")
    offset = overlay.offset
    w = offset.x1 - offset.x0
    h = offset.y1 - offset.y0
    return (
        f'<img {_OVERLAY_ATTR}="{index}" '
        f'class="rps-overlay-img" '
        f'src="data:image/svg+xml;base64,{b64}" '
        f'style="left:{offset.x0:.1f}pt;top:{offset.y0:.1f}pt;'
        f'width:{w:.1f}pt;height:{h:.1f}pt;" />'
    )


def _inject_overlays(
    body_html: str,
    overlays: Sequence[BakedOverlay],
) -> tuple[str, list[_OverlayInfo]]:
    """Inject overlay ``<img>`` elements inside their anchor block elements.

    Each overlay is positioned via ``position: absolute`` inside the
    anchor's ``position: relative`` container.  ``document_end`` overlays
    go in a trailing div.  Unresolved anchors go on an appended
    "Orphaned ink" page.

    Args:
        body_html: The HTML body string from ``ast_to_html``.
        overlays: Baked overlays to inject.

    Returns:
        ``(modified_body_html, overlay_info_list)``
    """
    info: list[_OverlayInfo] = []
    doc_end_imgs: list[str] = []
    orphan_imgs: list[str] = []

    # Build a mapping of anchor_ref -> list of (index, overlay) for
    # overlays that target a paragraph anchor.
    anchor_overlays: dict[str, list[tuple[int, BakedOverlay]]] = {}
    for idx, ov in enumerate(overlays):
        if ov.anchor == "document_end":
            doc_end_imgs.append(_overlay_img_tag(idx, ov))
            info.append(_OverlayInfo(index=idx, kind="document_end"))
        else:
            anchor_overlays.setdefault(ov.anchor, []).append((idx, ov))

    # Inject overlays into their anchor elements.
    # Strategy: for each anchor ref that has overlays, find the element
    # with data-anchor="<ref>" and insert the <img> before the closing tag,
    # adding class rps-has-overlay.
    modified = body_html
    for anchor_ref, ov_list in anchor_overlays.items():
        # Find the element with this data-anchor value.
        # The HTML from ast_to_html uses patterns like:
        #   <p data-anchor="hash#0">...</p>
        #   <div class="rps-list-item" data-anchor="hash#0">...</div>
        #   <h2 data-anchor="hash#0">...</h2>
        pattern = re.compile(
            r"(<(?:p|div|h[1-6])(?:\s[^>]*?)?"
            + ANCHOR_ATTR
            + r'="'
            + re.escape(anchor_ref)
            + r'"'
            + r"(?:\s[^>]*?)?>)"  # opening tag
            + r"(.*?)"  # inner content
            + r"(</(?:p|div|h[1-6])>)",  # closing tag
            re.DOTALL,
        )
        match = pattern.search(modified)
        if match:
            open_tag = match.group(1)
            inner = match.group(2)
            close_tag = match.group(3)

            # Add rps-has-overlay class to the opening tag
            if 'class="' in open_tag:
                open_tag = open_tag.replace('class="', 'class="rps-has-overlay ', 1)
            else:
                # Insert class before the closing >
                open_tag = open_tag[:-1] + ' class="rps-has-overlay">'

            imgs = "".join(_overlay_img_tag(i, o) for i, o in ov_list)
            for i, _ in ov_list:
                info.append(_OverlayInfo(index=i, kind="placed"))

            replacement = open_tag + inner + imgs + close_tag
            modified = modified[: match.start()] + replacement + modified[match.end() :]
        else:
            # Anchor not found → orphaned
            for i, o in ov_list:
                orphan_imgs.append(_overlay_img_tag(i, o))
                info.append(_OverlayInfo(index=i, kind="relocated"))

    # Append document_end container
    if doc_end_imgs:
        modified += '\n<div class="rps-doc-end-overlays">' + "".join(doc_end_imgs) + "</div>"

    # Append orphan page
    if orphan_imgs:
        modified += (
            '\n<div class="rps-orphan-page">'
            '<p style="font-size:9pt;color:#666;">Orphaned ink</p>'
            + "".join(orphan_imgs)
            + "</div>"
        )

    # Sort info by index for consistent ordering
    info.sort(key=lambda x: x.index)
    return modified, info


def _build_placements(
    overlays: Sequence[BakedOverlay],
    overlay_info: list[_OverlayInfo],
    overlay_boxes: dict[int, tuple[int, float, float, float, float]],
    page_count: int,
) -> tuple[Placed | Relocated, ...]:
    """Build placements from the actual laid-out overlay box positions.

    Reads each overlay's position from ``overlay_boxes`` (populated by
    the ``data-overlay`` walk of the box tree) so ``Placed.rect`` is
    where the pixels are, not arithmetic.

    I11: every overlay gets exactly one ``Placed`` or ``Relocated``.

    Args:
        overlays: The original overlays.
        overlay_info: Info from ``_inject_overlays`` (kind per overlay).
        overlay_boxes: Mapping overlay index -> (page, x, y, w, h) in CSS px.
        page_count: Total page count of the rendered document.

    Returns:
        Tuple with one ``Placed`` or ``Relocated`` per overlay.
    """
    placements: list[Placed | Relocated] = []
    orphan_page = max(0, page_count - 1)

    # Build info map for quick lookup
    info_map: dict[int, _OverlayInfo] = {oi.index: oi for oi in overlay_info}

    for idx, overlay in enumerate(overlays):
        oi = info_map.get(idx)
        box = overlay_boxes.get(idx)

        if box is not None:
            pg, bx, by, bw, bh = box
            rect = Rect(
                x0=bx * _CSS_PX_TO_PT,
                y0=by * _CSS_PX_TO_PT,
                x1=(bx + bw) * _CSS_PX_TO_PT,
                y1=(by + bh) * _CSS_PX_TO_PT,
            )
            if oi is not None and oi.kind == "relocated":
                placements.append(Relocated(overlay=overlay, page=pg, rect=rect))
            else:
                placements.append(Placed(overlay=overlay, page=pg, rect=rect))
        else:
            # Overlay not found in box tree (should not happen if HTML was
            # injected correctly).  Fall back to Relocated on last page.
            placements.append(Relocated(overlay=overlay, page=orphan_page, rect=overlay.offset))

    return tuple(placements)
