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

import hashlib
import logging
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import weasyprint

from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.device_profile import DeviceProfile
from rock_paper_sync.parser import MarkdownDocument
from rock_paper_sync.render.html import TOKEN_ATTR, ast_to_html
from rock_paper_sync.render.wordmap import (
    BakedOverlay,
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

h1 {{ font-size: {font_size * 1.8}pt; margin: 0.5em 0 0.3em 0; }}
h2 {{ font-size: {font_size * 1.4}pt; margin: 0.5em 0 0.3em 0; }}
h3 {{ font-size: {font_size * 1.2}pt; margin: 0.4em 0 0.2em 0; }}
h4, h5, h6 {{ font-size: {font_size * 1.1}pt; margin: 0.4em 0 0.2em 0; }}

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


def _walk_box_tree(
    page_box: object,
    token_boxes: dict[int, list[tuple[int, float, float, float, float]]],
    page_idx: int,
) -> None:
    """Walk a WeasyPrint page box tree and collect token positions.

    Walks recursively, propagating the nearest ancestor's ``data-token``
    value downward so that text boxes nested inside formatting elements
    (``<strong>``, ``<em>``, etc.) inherit the token id from their
    ancestor ``<span data-token="N">``.

    Args:
        page_box: WeasyPrint page box (from ``document.pages[i]._page_box``).
        token_boxes: Dict mapping token_id -> list of (page, x, y, w, h) in CSS px.
        page_idx: Zero-based page index.
    """

    def _recurse(box: object, inherited_token: int | None) -> None:
        element = getattr(box, "element", None)
        current_token = inherited_token

        # Check if this box's element carries a data-token
        if element is not None:
            token_val = element.get(TOKEN_ATTR) if hasattr(element, "get") else None
            if token_val is not None:
                try:
                    current_token = int(token_val)
                except (ValueError, TypeError):
                    pass

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

            if is_inline_block:
                return  # Don't recurse into inline-block children

        # Recurse into children
        for child in getattr(box, "children", []):
            _recurse(child, current_token)

    _recurse(page_box, None)


def _boxes_to_wordmap(
    token_boxes: dict[int, list[tuple[int, float, float, float, float]]],
    token_texts: list[str],
    page_size_pt: tuple[float, float],
) -> WordMap:
    """Convert collected boxes into a WordMap.

    CSS px are converted to PDF points (* 0.75).

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

        for page_idx, px_x, px_y, px_w, px_h in boxes:
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

    return WordMap(
        page_size=page_size_pt,
        pages=tuple(pages),
        texts=tuple(token_texts),
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

        # If overlays exist, inject overlay HTML before closing
        overlay_html = ""
        if overlays:
            overlay_html = _build_overlay_html(overlays)

        # Build CSS
        css_str = _build_css(self._layout, profile)

        # Full HTML document
        full_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{css_str}</style></head>
<body>{body_html}{overlay_html}</body>
</html>"""

        # Render with WeasyPrint
        html_doc = weasyprint.HTML(string=full_html)
        wp_doc = html_doc.render(font_config=self._wp_font_config)

        # Walk the box tree to build token positions
        token_boxes: dict[int, list[tuple[int, float, float, float, float]]] = defaultdict(list)

        # Collect token texts from the HTML (reading order)
        # We need to reconstruct the token list from the AST
        token_texts = _collect_token_texts(doc, self._layout.trailing_blank_pages)

        for page_idx, page in enumerate(wp_doc.pages):
            _walk_box_tree(page._page_box, token_boxes, page_idx)

        # Build word map (asserts I1)
        word_map = _boxes_to_wordmap(
            token_boxes,
            token_texts,
            profile.page_size_pt,
        )

        # Compute placements for overlays (I11: total)
        placements = _compute_placements(overlays, word_map, len(wp_doc.pages), profile)

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


def _build_overlay_html(overlays: Sequence[BakedOverlay]) -> str:
    """Build HTML for overlay images appended after the body content.

    Overlays are rendered as visible images placed after the last body
    block (document_end anchors) or inlined (paragraph anchors handled
    by the CSS layout flow). For now, all overlays are placed at the
    end of the document body as inline images.

    Returns:
        HTML string to append to the body.
    """
    import base64

    parts: list[str] = []
    for overlay in overlays:
        b64 = base64.b64encode(overlay.svg).decode("ascii")
        # Render as an inline image; the CSS controls sizing
        w = overlay.offset.x1 - overlay.offset.x0
        h = overlay.offset.y1 - overlay.offset.y0
        parts.append(
            f'<div class="rps-overlay">'
            f'<img src="data:image/svg+xml;base64,{b64}" '
            f'style="width:{w:.1f}pt;height:{h:.1f}pt;" />'
            f"</div>"
        )
    return "\n".join(parts)


def _compute_placements(
    overlays: Sequence[BakedOverlay],
    word_map: WordMap,
    page_count: int,
    profile: DeviceProfile,
) -> tuple[Placed | Relocated, ...]:
    """Compute a placement for every overlay (I11: total).

    For each overlay:
    - If anchor == "document_end": place on the last body page.
    - If anchor matches a paragraph in the word map: place on that page.
    - Otherwise: ``Relocated`` to an orphaned ink page (last page + 1).

    Returns:
        Tuple with one ``Placed`` or ``Relocated`` per overlay.
    """
    placements: list[Placed | Relocated] = []
    orphan_page = page_count  # hypothetical appended page

    for overlay in overlays:
        anchor = overlay.anchor
        offset = overlay.offset

        if anchor == "document_end":
            # Place on the last content page
            last_page = max(0, len(word_map.pages) - 1) if word_map.pages else 0
            placements.append(Placed(overlay=overlay, page=last_page, rect=offset))
        else:
            # Try to place on the first page that has tokens
            # (anchor matching by ref is done in pull_sync; here we just need
            # to put the overlay somewhere reasonable on a content page)
            placed = False
            for pw in word_map.pages:
                if pw.tokens:
                    placements.append(Placed(overlay=overlay, page=pw.page, rect=offset))
                    placed = True
                    break

            if not placed:
                # Anchor not found -> Relocated to orphan page
                placements.append(Relocated(overlay=overlay, page=orphan_page, rect=offset))

    return tuple(placements)
