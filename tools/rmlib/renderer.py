"""Renderer for reMarkable .rm files.

Renders .rm files to PNG images, validated against device thumbnails.
This replaces the unreliable rmc tool for visual comparison testing.

Layout model (Layer A of docs/DEVICE_REPLICA_DESIGN.md):
    This renderer contains NO private layout math. Every layout quantity
    comes from the production engine:

    - Line breaks: WordWrapLayoutEngine.calculate_line_breaks at
      DeviceGeometry.effective_layout_width
    - Char/line y positions: LayoutContext.offset_to_position (context built
      from the .rm file's own RootTextBlock via RmFileExtractor)
    - Line pitch and baseline: DeviceGeometry.line_height / baseline_offset
    - Stroke placement: AnchorResolver + AnchorRelativePoint.to_document
      (including the NEGATIVE_Y_OFFSET rule for native_y < 0)
    - Page transform: page_x = page_width / 2 + doc_x (PAGE_CENTER_X from
      the production coordinates module)

    The only constants defined here are presentation-only (colors, stroke
    width scaling, the heading font ratio pending corpus measurement).

Usage:
    from tools.rmlib import RmRenderer

    renderer = RmRenderer()
    image = renderer.render(Path("page.rm"))
    renderer.save_png(Path("page.rm"), Path("output.png"))
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import rmscene
from PIL import Image, ImageDraw, ImageFont
from rmscene import CrdtId, RootTextBlock, SceneGlyphItemBlock, SceneLineItemBlock, si

from rock_paper_sync.coordinates import (
    PAGE_CENTER_X,
    AnchorPoint,
    AnchorRelativePoint,
    AnchorResolver,
    is_root_layer,
)
from rock_paper_sync.layout import LayoutContext
from rock_paper_sync.layout.device import DEFAULT_DEVICE
from rock_paper_sync.rm_file_extractor import RmFileExtractor

from .colors import BACKGROUND_COLOR, get_highlight_rgba, get_pen_color

logger = logging.getLogger(__name__)

# Page dimensions come from the production device geometry (single source
# of truth), not local literals.
DEFAULT_WIDTH = DEFAULT_DEVICE.page_width
DEFAULT_HEIGHT = int(DEFAULT_DEVICE.page_height)

# Stroke width scaling (presentation-only)
# Device stroke widths (12-18) need to be scaled down for rendering.
# A ballpoint pen should render as ~1-2 pixel width.
STROKE_WIDTH_SCALE = 0.15

# Minimum highlighter stroke width in pixels and its device-width multiplier
# (presentation-only: how thick the semi-transparent band is painted).
HIGHLIGHTER_MIN_WIDTH = 15
HIGHLIGHTER_WIDTH_SCALE = 2

# Text rendering parameters (presentation-only)
TEXT_COLOR = (0, 0, 0)  # Black text

# OPEN: heading font scale relative to body (single heading tier for now).
# The device has TWO tiers (Title, Heading — see DEVICE_REPLICA_DESIGN.md §1);
# their pixel sizes are on the Phase 8 corpus probe list. Until measured, we
# keep one heading tier at this visually-calibrated ratio of the body font
# (DeviceGeometry.font_pixel_size). This is the ONE place the ratio lives.
HEADING_FONT_SCALE = 2.07

# Font search paths (same as font_metrics.py - Noto Sans Regular is used
# by the production font metrics; Layer B will swap in reMarkableSans).
FONT_SEARCH_PATHS = [
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",  # Arch Linux
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",  # Debian/Ubuntu
    "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",  # Fedora
    "/System/Library/Fonts/Supplemental/NotoSans-Regular.ttf",  # macOS
    str(Path.home() / ".local/share/fonts/NotoSans-Regular.ttf"),  # User fonts
]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load Noto Sans Regular at the specified size."""
    for path in FONT_SEARCH_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    # Fall back to default
    logger.warning("Noto Sans Regular not found, using default font")
    return ImageFont.load_default()


# Pre-load fonts at sizes derived from production geometry.
BODY_FONT = _load_font(round(DEFAULT_DEVICE.font_pixel_size))
HEADING_FONT = _load_font(round(DEFAULT_DEVICE.font_pixel_size * HEADING_FONT_SCALE))


def _page_x(doc_x: float) -> float:
    """Convert a center-relative document x to absolute page x."""
    return PAGE_CENTER_X + doc_x


def compute_text_line_layout(
    layout_ctx: LayoutContext,
) -> list[tuple[int, int, float, float]]:
    """Compute visual text lines and positions from the production engine.

    This is the single layout path used by both the renderer and the D3
    wiring test (tests/layout/test_d3_renderer_wiring.py): line breaks come
    from WordWrapLayoutEngine.calculate_line_breaks at the device's
    effective_layout_width, and each line's (x, y) comes from
    LayoutContext.offset_to_position for the line's start offset.

    Returns:
        List of (start_offset, end_offset, doc_x, doc_y) per visual line.
        doc_x is center-relative; doc_y is absolute page y.
    """
    text = layout_ctx.text_content
    if not text:
        return []

    layout_width = layout_ctx.geometry.effective_layout_width
    line_breaks = layout_ctx.engine.calculate_line_breaks(text, layout_width)

    lines: list[tuple[int, int, float, float]] = []
    for i, start in enumerate(line_breaks):
        end = line_breaks[i + 1] if i + 1 < len(line_breaks) else len(text)
        x, y = layout_ctx.offset_to_position(start)
        lines.append((start, end, x, y))
    return lines


def compute_anchor_points(
    resolver: AnchorResolver,
    blocks: list,
) -> dict[CrdtId, AnchorPoint]:
    """Resolve production AnchorPoints for every stroke parent in blocks.

    Shared by the renderer and the D3 wiring test. All y values come from
    AnchorResolver (which resolves char offsets via
    LayoutContext.offset_to_position); no local adjustment is applied.
    """
    anchor_points: dict[CrdtId, AnchorPoint] = {}
    parent_ids = {b.parent_id for b in blocks if isinstance(b, SceneLineItemBlock)}
    for parent_id in parent_ids:
        anchor = resolver.get_anchor(parent_id)
        if anchor is not None:
            anchor_points[parent_id] = anchor
    return anchor_points


class RmRenderer:
    """Renders reMarkable .rm files to PNG images.

    This renderer is designed to be validated against device thumbnails.
    It extracts strokes and highlights from .rm files and draws them
    to a PIL Image with the correct coordinate transformations.

    Layer filtering:
        By default only visible layers are rendered. Pass include_hidden=True
        to include hidden layers (e.g. the PRESERVATION layer for orphaned
        annotations). Pass layer_filter to render only specific layers.

    Example:
        renderer = RmRenderer()
        image = renderer.render(Path("page.rm"))
        image.save("output.png")

        # Render only the preservation layer (CrdtId(0, 21))
        from rmscene import CrdtId
        image = renderer.render_bytes(data, layer_filter={CrdtId(0, 21)})
    """

    def __init__(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        background_color: tuple[int, int, int] = BACKGROUND_COLOR,
    ):
        """Initialize renderer with page dimensions.

        Args:
            width: Page width in pixels (default: device page width)
            height: Page height in pixels (default: device page height)
            background_color: RGB tuple for background (default: white)
        """
        self.width = width
        self.height = height
        self.background_color = background_color

    def render(self, rm_path: Path, **kwargs) -> Image.Image:
        """Render .rm file to PIL Image.

        Args:
            rm_path: Path to .rm file
            **kwargs: Forwarded to render_bytes (include_hidden, layer_filter)

        Returns:
            PIL Image with rendered content
        """
        with open(rm_path, "rb") as f:
            return self.render_bytes(f.read(), **kwargs)

    def render_pages(self, rm_bytes_list: list[bytes], **kwargs) -> list[Image.Image]:
        """Render a multi-page document, one .rm file per page.

        Each .rm file IS one page — pagination is decided by the production
        generator upstream, never re-derived here.

        Args:
            rm_bytes_list: Raw bytes of each page's .rm file, in page order
            **kwargs: Forwarded to render_bytes (include_hidden, layer_filter)

        Returns:
            List of PIL Images, one per page
        """
        return [self.render_bytes(rm_bytes, **kwargs) for rm_bytes in rm_bytes_list]

    def render_bytes(
        self,
        rm_bytes: bytes,
        include_hidden: bool = False,
        layer_filter: set[CrdtId] | None = None,
    ) -> Image.Image:
        """Render .rm bytes to PIL Image.

        Args:
            rm_bytes: Raw bytes of .rm file
            include_hidden: If True, render items on hidden layers too.
                Ignored when layer_filter is set. Default False.
            layer_filter: If set, render ONLY items on these layer node_ids.
                E.g. {CrdtId(0, 21)} renders only the PRESERVATION layer.

        Returns:
            PIL Image with rendered content
        """
        # Parse blocks
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))

        # Build layer membership info
        layer_visibility, group_to_layer = self._build_layer_info(blocks)

        # Compute allowed layer set for filtering
        if layer_filter is not None:
            allowed_layers: set[CrdtId] | None = layer_filter
        elif not include_hidden:
            allowed_layers = {nid for nid, vis in layer_visibility.items() if vis}
        else:
            allowed_layers = None  # no filtering

        # Production extraction: text content, origin, and layout context all
        # come from the .rm file's own RootTextBlock via RmFileExtractor.
        extractor = RmFileExtractor.from_blocks(blocks)
        layout_ctx = extractor.get_layout_context(DEFAULT_DEVICE)
        paragraph_styles = self._extract_paragraph_styles(blocks, extractor.crdt_to_char)

        # Production anchor resolution (CRDT -> char offset -> engine y).
        resolver = AnchorResolver.from_extractor(extractor)
        anchor_points = compute_anchor_points(resolver, blocks)

        # Create image with RGBA for alpha blending (highlights)
        image = Image.new("RGBA", (self.width, self.height), (*self.background_color, 255))
        draw = ImageDraw.Draw(image)

        # Render text first (background layer — always shown for context)
        self._render_text(draw, layout_ctx, paragraph_styles)

        # Extract and render strokes (filtered by layer)
        strokes = self._extract_strokes(blocks, allowed_layers, group_to_layer, layer_visibility)
        for stroke in strokes:
            self._render_stroke(draw, image, stroke, anchor_points)

        # Extract and render highlights (filtered by layer)
        highlights = self._extract_highlights(blocks, allowed_layers)
        for highlight in highlights:
            self._render_highlight(image, highlight)

        # Convert to RGB for final output
        return image.convert("RGB")

    def _build_layer_info(
        self, blocks: list
    ) -> tuple[dict[CrdtId, bool], dict[CrdtId, CrdtId]]:
        """Build layer visibility map and group-value-to-layer map.

        Returns:
            layer_visibility: maps layer_node_id -> visible (bool)
            group_to_layer: maps SceneGroupItemBlock.item.value -> parent layer node_id
        """
        layer_visibility: dict[CrdtId, bool] = {}
        group_to_layer: dict[CrdtId, CrdtId] = {}

        for block in blocks:
            bname = type(block).__name__
            if bname == "TreeNodeBlock":
                nid = block.group.node_id
                vis = block.group.visible.value if hasattr(block.group.visible, "value") else True
                layer_visibility[nid] = vis
            elif bname == "SceneGroupItemBlock":
                if hasattr(block, "item") and hasattr(block.item, "value"):
                    group_to_layer[block.item.value] = block.parent_id

        return layer_visibility, group_to_layer

    def _stroke_layer(
        self,
        stroke,
        layer_visibility: dict[CrdtId, bool],
        group_to_layer: dict[CrdtId, CrdtId],
    ) -> CrdtId | None:
        """Return the layer node_id for a stroke, or None if unknown.

        Stroke parent_id is the stroke's own TreeNodeBlock node_id, which
        also appears in layer_visibility (the stroke bundle has its own
        TreeNodeBlock). We must walk group_to_layer first to find the
        actual containing layer, not the stroke's own node.
        """
        pid = stroke.parent_id
        # Walk group_to_layer to find the actual containing layer.
        # This must be checked before layer_visibility because stroke bundle
        # nodes also appear in layer_visibility (they have their own TreeNodeBlock).
        parent = group_to_layer.get(pid)
        if parent in layer_visibility:
            return parent
        # One more level for nested groups
        if parent is not None:
            grandparent = group_to_layer.get(parent)
            if grandparent in layer_visibility:
                return grandparent
        # Direct parent is a layer (e.g. unanchored strokes without a SceneGroupItemBlock)
        if pid in layer_visibility:
            return pid
        return None

    def save_png(self, rm_path: Path, output_path: Path, **kwargs) -> None:
        """Render .rm file and save as PNG.

        Args:
            rm_path: Path to .rm file
            output_path: Path for output PNG
            **kwargs: Forwarded to render_bytes (include_hidden, layer_filter)
        """
        image = self.render(rm_path, **kwargs)
        image.save(output_path, "PNG")

    def _extract_strokes(
        self,
        blocks: list,
        allowed_layers: set[CrdtId] | None,
        group_to_layer: dict[CrdtId, CrdtId],
        layer_visibility: dict[CrdtId, bool] | None = None,
    ) -> list[SceneLineItemBlock]:
        """Extract stroke blocks, filtered to allowed layers."""
        strokes = [b for b in blocks if isinstance(b, SceneLineItemBlock)]
        if allowed_layers is None:
            return strokes
        lv = layer_visibility or {}
        result = []
        for s in strokes:
            layer = self._stroke_layer(s, lv, group_to_layer)
            if layer in allowed_layers:
                result.append(s)
        return result

    def _extract_highlights(
        self,
        blocks: list,
        allowed_layers: set[CrdtId] | None,
    ) -> list[SceneGlyphItemBlock]:
        """Extract highlight blocks, filtered to allowed layers."""
        highlights = [b for b in blocks if isinstance(b, SceneGlyphItemBlock)]
        if allowed_layers is None:
            return highlights
        return [h for h in highlights if h.parent_id in allowed_layers]

    def _extract_paragraph_styles(
        self, blocks: list, crdt_to_char: dict[CrdtId, int]
    ) -> dict[int, si.ParagraphStyle]:
        """Extract paragraph styles (char offset -> style) from RootTextBlock.

        Text content and CRDT mapping come from RmFileExtractor; this only
        resolves the style map (used for heading font selection).
        """
        for block in blocks:
            if isinstance(block, RootTextBlock):
                text_data = block.value
                paragraph_styles: dict[int, si.ParagraphStyle] = {}
                if hasattr(text_data, "styles") and text_data.styles:
                    for style_crdt_id, lww_value in text_data.styles.items():
                        # Style CRDT IDs may have different author IDs (part1) than
                        # text items. For system-generated styles (part1=0), part2
                        # is the character offset. For user-generated styles, look
                        # up in the crdt_to_char map.
                        if style_crdt_id.part1 == 0:
                            actual_char_offset = style_crdt_id.part2
                        elif style_crdt_id in crdt_to_char:
                            actual_char_offset = crdt_to_char[style_crdt_id]
                        else:
                            continue
                        paragraph_styles[actual_char_offset] = lww_value.value
                return paragraph_styles
        return {}

    def _render_text(
        self,
        draw: ImageDraw.Draw,
        layout_ctx: LayoutContext,
        paragraph_styles: dict[int, si.ParagraphStyle],
    ) -> None:
        """Render text content using positions from the production engine.

        All line breaks and (x, y) positions come from
        compute_text_line_layout (production WordWrapLayoutEngine /
        LayoutContext); this method only paints.
        """
        text = layout_ctx.text_content
        if not text:
            return

        # Map each paragraph start offset to its style, so a visual line can
        # inherit the style of the paragraph containing it.
        paragraph_starts: list[tuple[int, si.ParagraphStyle]] = []
        offset = 0
        for paragraph in text.split("\n"):
            style = paragraph_styles.get(offset, si.ParagraphStyle.BASIC)
            paragraph_starts.append((offset, style))
            offset += len(paragraph) + 1  # +1 for newline

        def style_for(line_start: int) -> si.ParagraphStyle:
            current = si.ParagraphStyle.BASIC
            for para_start, style in paragraph_starts:
                if para_start > line_start:
                    break
                current = style
            return current

        for start, end, doc_x, doc_y in compute_text_line_layout(layout_ctx):
            line_text = text[start:end].rstrip("\n").rstrip()
            if not line_text:
                continue
            is_heading = style_for(start) == si.ParagraphStyle.HEADING
            font = HEADING_FONT if is_heading else BODY_FONT
            draw.text((_page_x(doc_x), doc_y), line_text, fill=TEXT_COLOR, font=font)

    def _render_stroke(
        self,
        draw: ImageDraw.Draw,
        image: Image.Image,
        stroke: SceneLineItemBlock,
        anchor_points: dict[CrdtId, AnchorPoint],
    ) -> None:
        """Render a single stroke to the image.

        Args:
            draw: PIL ImageDraw object
            image: PIL Image for alpha blending
            stroke: SceneLineItemBlock containing stroke data
            anchor_points: Map from parent_id to production AnchorPoint
        """
        if not stroke.item or not stroke.item.value:
            return

        line: si.Line = stroke.item.value

        if not line.points:
            return

        points = self._stroke_page_points(stroke.parent_id, line, anchor_points)

        # Get pen color
        color = get_pen_color(line.color)

        # Check if this is a highlighter stroke
        is_highlighter = line.tool in (si.Pen.HIGHLIGHTER_1, si.Pen.HIGHLIGHTER_2)

        if is_highlighter:
            # Draw highlighter as semi-transparent thick line
            self._render_highlighter_stroke(image, line, points)
        else:
            # Draw regular stroke
            self._render_regular_stroke(draw, line, points, color)

    def _stroke_page_points(
        self,
        parent_id: CrdtId,
        line: si.Line,
        anchor_points: dict[CrdtId, AnchorPoint],
    ) -> list[tuple[float, float]]:
        """Convert native stroke points to absolute page coordinates.

        Uses the production coordinate model:
        - Root-layer strokes are center-relative document coordinates.
        - Anchored strokes are AnchorRelativePoints; to_document applies
          anchor_origin_x, the resolved anchor y, and the NEGATIVE_Y_OFFSET
          rule for native_y < 0.
        - Page x = page_width / 2 + doc x in both cases.
        """
        anchor = anchor_points.get(parent_id)
        if anchor is None or is_root_layer(parent_id):
            # Center-relative document coordinates (no anchor transform).
            return [(_page_x(p.x), p.y) for p in line.points]

        page_points: list[tuple[float, float]] = []
        for p in line.points:
            doc = AnchorRelativePoint(p.x, p.y).to_document(anchor)
            page_points.append((_page_x(doc.x), doc.y))
        return page_points

    def _render_regular_stroke(
        self,
        draw: ImageDraw.Draw,
        line: si.Line,
        points: list[tuple[float, float]],
        color: tuple[int, int, int],
    ) -> None:
        """Render a regular (non-highlighter) stroke."""
        if len(points) < 2:
            # Draw a single point as a small circle
            if points:
                x, y = points[0]
                r = max(1, int(line.points[0].width * STROKE_WIDTH_SCALE / 2))
                draw.ellipse([x - r, y - r, x + r, y + r], fill=color)
            return

        # Scale width for realistic ballpoint pen appearance
        # Device stroke widths (12-18) are scaled to ~1-3 pixel width
        width = max(1, int(line.points[0].width * STROKE_WIDTH_SCALE))

        # Draw as connected line segments
        draw.line(points, fill=color, width=width)

    def _render_highlighter_stroke(
        self,
        image: Image.Image,
        line: si.Line,
        points: list[tuple[float, float]],
    ) -> None:
        """Render a highlighter stroke as semi-transparent yellow."""
        if len(points) < 2:
            return

        # Get highlighter color with alpha
        highlight_rgba = get_highlight_rgba()

        # Create overlay for alpha blending
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        # Use thick line width for highlighter effect
        width = max(HIGHLIGHTER_MIN_WIDTH, int(line.points[0].width * HIGHLIGHTER_WIDTH_SCALE))

        # Draw thick line
        overlay_draw.line(points, fill=highlight_rgba, width=width)

        # Composite onto main image
        image.alpha_composite(overlay)

    def _render_highlight(
        self,
        image: Image.Image,
        highlight: SceneGlyphItemBlock,
    ) -> None:
        """Render a text highlight as semi-transparent rectangles.

        Args:
            image: PIL Image for alpha blending
            highlight: SceneGlyphItemBlock containing highlight data

        Coordinate system:
            Highlight rectangles are in center-relative document coordinates
            (x=0 is page center); y is already absolute. The x transform is
            the same page transform used for text and strokes.
        """
        if not hasattr(highlight.item, "value") or not highlight.item.value:
            return

        glyph_value = highlight.item.value

        if not hasattr(glyph_value, "rectangles") or not glyph_value.rectangles:
            return

        # Get highlight color with alpha
        highlight_rgba = get_highlight_rgba()

        # Create overlay for alpha blending
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        for rect in glyph_value.rectangles:
            # Convert center-relative document x to page x (same helper as
            # text and strokes); y is already in page coordinates.
            x1 = _page_x(rect.x)
            y1 = rect.y
            x2 = x1 + rect.w
            y2 = y1 + rect.h

            overlay_draw.rectangle([x1, y1, x2, y2], fill=highlight_rgba)

        # Composite onto main image
        image.alpha_composite(overlay)


def render_rm_file(rm_path: Path, output_path: Path) -> bool:
    """Convenience function to render .rm file to PNG.

    Args:
        rm_path: Path to .rm file
        output_path: Path for output PNG

    Returns:
        True if rendering succeeded
    """
    try:
        renderer = RmRenderer()
        renderer.save_png(rm_path, output_path)
        return True
    except Exception as e:
        logger.error(f"Failed to render {rm_path}: {e}")
        return False
