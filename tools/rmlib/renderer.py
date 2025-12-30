"""Renderer for reMarkable .rm files.

Renders .rm files to PNG images, validated against device thumbnails.
This replaces the unreliable rmc tool for visual comparison testing.

Coordinate Model:
    See docs/RENDERER_COORDINATE_MODEL.md for detailed documentation on:
    - Page coordinate system (1404x1872 @ 226 DPI)
    - Text width from RootTextBlock (not calculated)
    - Line height values (57px for coordinates, not 68px)
    - Stroke baseline offset (20px for proper alignment)
    - Highlight rectangle positioning (absolute coords)

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

from src.rock_paper_sync.coordinate_transformer import (
    END_OF_DOC_ANCHOR_MARKER,
    ParentAnchorResolver,
)

from .colors import BACKGROUND_COLOR, get_highlight_rgba, get_pen_color

logger = logging.getLogger(__name__)

# reMarkable page dimensions (from device geometry)
DEFAULT_WIDTH = 1404
DEFAULT_HEIGHT = 1872

# Page center X (coordinates are relative to center)
PAGE_CENTER_X = 702.0  # 1404 / 2

# Text origin offset (from device geometry)
TEXT_ORIGIN_X = -375.0
TEXT_ORIGIN_Y = 234.0

# Typography model (calibrated 2025-12-28 against device thumbnails)
#
# Coordinate Systems:
#   - Document coordinates: 1404×1872 @ 226 DPI (reMarkable 2 format)
#   - Physical display: varies by device (Paper Pro Move: 264 PPI)
#   - Thumbnails: rendered from physical display, then scaled
#
# The Key Insight:
#   Visual line height = CRDT anchor height × (PHYS_PPI / DOC_DPI)
#   = 57px × (264/226) = 57 × 1.168 = 66.6px
#
#   The CRDT anchor line height (57px in device.py) is for positioning in
#   document coordinates. When the device renders to physical pixels and
#   generates thumbnails, lines appear ~17% taller due to the PPI difference.
#
# Calibrated Values (from overlay comparison with device thumbnails):
#   - BODY_LINE_HEIGHT = 68px (theory: 66.6px, calibrated: 68px)
#   - HEADING_LINE_HEIGHT = 104px (~1.53× body)
#   - BODY_FONT_SIZE = 31px (10pt @ 226 DPI = 31.4px)
#   - HEADING_FONT_SIZE = 65px (~2.1× body)
#
# For anchor positioning (stroke Y-coordinates), we also use the scaled
# line height (68px) because we compare our rendered output to thumbnails.
#
LINE_HEIGHT = 68.0  # Visual line height (57 × 264/226, for thumbnail comparison)
CHARS_PER_LINE = 50  # Approximate chars per line (for fallback)

# Stroke width scaling
# Device stroke widths (12-18) need to be scaled down for rendering
# A ballpoint pen should render as ~1-2 pixel width
STROKE_WIDTH_SCALE = 0.15  # Scale factor for stroke width

# Text rendering parameters
TEXT_COLOR = (0, 0, 0)  # Black text
# Use 57px line height to match device coordinate system for highlights/strokes
# (68px was calibrated for thumbnail comparison, but coordinates use 57px)
BODY_LINE_HEIGHT = 57  # Device anchor line height
HEADING_LINE_HEIGHT = 87  # ~1.53× body line height (57 × 1.53)
BODY_FONT_SIZE = 31  # 10pt at 226 DPI = 31.4px
HEADING_FONT_SIZE = 65  # ~2.1× body (from visual calibration)

# Stroke baseline offset
# Device anchors strokes to the text baseline, but our char_to_y map stores
# the top of each text line. This offset shifts stroke anchor Y down to the
# baseline so handwritten annotations align with text they were written on.
# Calibrated: 20px positions descenders to touch underscore characters.
STROKE_BASELINE_OFFSET = 20

# Font search paths (same as font_metrics.py - Noto Sans Regular is used on reMarkable)
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


# Pre-load fonts at common sizes
BODY_FONT = _load_font(BODY_FONT_SIZE)
HEADING_FONT = _load_font(HEADING_FONT_SIZE)


class RmRenderer:
    """Renders reMarkable .rm files to PNG images.

    This renderer is designed to be validated against device thumbnails.
    It extracts strokes and highlights from .rm files and draws them
    to a PIL Image with the correct coordinate transformations.

    Example:
        renderer = RmRenderer()
        image = renderer.render(Path("page.rm"))
        image.save("output.png")
    """

    def __init__(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        background_color: tuple[int, int, int] = BACKGROUND_COLOR,
    ):
        """Initialize renderer with page dimensions.

        Args:
            width: Page width in pixels (default: 1404)
            height: Page height in pixels (default: 1872)
            background_color: RGB tuple for background (default: white)
        """
        self.width = width
        self.height = height
        self.background_color = background_color

    def render(self, rm_path: Path) -> Image.Image:
        """Render .rm file to PIL Image.

        Args:
            rm_path: Path to .rm file

        Returns:
            PIL Image with rendered content
        """
        with open(rm_path, "rb") as f:
            return self.render_bytes(f.read())

    def render_bytes(self, rm_bytes: bytes) -> Image.Image:
        """Render .rm bytes to PIL Image.

        Args:
            rm_bytes: Raw bytes of .rm file

        Returns:
            PIL Image with rendered content
        """
        # Parse blocks
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))

        # Extract text first (needed for text rendering and anchor positioning)
        text, text_origin_x, text_origin_y, text_width, paragraph_styles = (
            self._extract_text_with_styles(blocks)
        )

        # Use ParentAnchorResolver for CRDT ID -> char offset mapping,
        # then compute visual coordinates ourselves
        anchor_map = self._build_anchor_map(
            blocks, text, text_origin_x, text_origin_y, text_width, paragraph_styles
        )

        # Create image with RGBA for alpha blending (highlights)
        image = Image.new("RGBA", (self.width, self.height), (*self.background_color, 255))
        draw = ImageDraw.Draw(image)

        # Render text first (background layer)
        self._render_text(draw, text, text_origin_x, text_origin_y, text_width, paragraph_styles)

        # Extract and render strokes
        strokes = self._extract_strokes(blocks)
        for stroke in strokes:
            self._render_stroke(draw, image, stroke, anchor_map)

        # Extract and render highlights
        highlights = self._extract_highlights(blocks)
        for highlight in highlights:
            self._render_highlight(image, highlight)

        # Convert to RGB for final output
        return image.convert("RGB")

    def save_png(self, rm_path: Path, output_path: Path) -> None:
        """Render .rm file and save as PNG.

        Args:
            rm_path: Path to .rm file
            output_path: Path for output PNG
        """
        image = self.render(rm_path)
        image.save(output_path, "PNG")

    def _build_anchor_map(
        self,
        blocks: list,
        text: str,
        text_origin_x: float,
        text_origin_y: float,
        text_width: float | None,
        paragraph_styles: dict[int, si.ParagraphStyle],
    ) -> dict[CrdtId, tuple[float, float]]:
        """Build map from parent_id to page coordinate offsets.

        Uses ParentAnchorResolver for CRDT ID -> character offset mapping,
        then computes visual Y positions accounting for word wrapping and
        different line heights for headings vs body text.

        Coordinate transformation:
        - ParentAnchorResolver provides: anchor_x (text-relative), char_offset
        - We compute: page_x = PAGE_CENTER_X + anchor_x
        - We compute: page_y using cumulative line heights with word wrapping

        Args:
            blocks: All rmscene blocks
            text: Full text content for computing line positions
            text_origin_x: X coordinate of text origin (text-relative)
            text_origin_y: Y coordinate of text origin
            text_width: Text width from .rm file (for word wrapping), or None for default
            paragraph_styles: Map of character offset to paragraph style

        Returns:
            Dict mapping parent_id -> (page_offset_x, page_offset_y)
        """
        from rock_paper_sync.layout import WordWrapLayoutEngine

        # Use ParentAnchorResolver for CRDT ID -> char offset mapping
        resolver = ParentAnchorResolver.from_blocks(blocks)

        # Use text_width from .rm file if available, otherwise calculate from page width
        if text_width is not None:
            max_text_width = text_width
        else:
            page_x = PAGE_CENTER_X + text_origin_x
            max_text_width = self.width - page_x - 50  # 50px right margin

        # Build a map of character offset -> cumulative Y position
        # This accounts for word wrapping AND different line heights for headings
        char_to_y: dict[int, float] = {}

        if text:
            engine = WordWrapLayoutEngine(
                text_width=max_text_width,
                use_font_metrics=True,
            )

            # Process each paragraph separately to handle different line heights
            current_y = text_origin_y
            current_offset = 0

            for paragraph in text.split("\n"):
                # Get style for this paragraph
                style = paragraph_styles.get(current_offset, si.ParagraphStyle.BASIC)
                is_heading = style == si.ParagraphStyle.HEADING
                line_height = HEADING_LINE_HEIGHT if is_heading else BODY_LINE_HEIGHT

                if paragraph:
                    # Calculate line breaks within this paragraph
                    para_line_breaks = engine.calculate_line_breaks(
                        paragraph, max_text_width
                    )

                    # Track the Y of the last visual line for newline mapping
                    last_line_y = current_y

                    # Map each character in the paragraph to its Y position
                    for i, break_pos in enumerate(para_line_breaks):
                        if i + 1 < len(para_line_breaks):
                            end_pos = para_line_breaks[i + 1]
                        else:
                            end_pos = len(paragraph)

                        # All chars on this visual line have the same Y
                        for j in range(break_pos, end_pos):
                            char_to_y[current_offset + j] = current_y

                        last_line_y = current_y  # Track last line Y before incrementing
                        current_y += line_height

                    # Map newline to the same Y as the last visual line
                    # (not the next line's Y)
                    char_to_y[current_offset + len(paragraph)] = last_line_y
                else:
                    # Empty line - map newline to current Y, then add line height
                    char_to_y[current_offset] = current_y
                    current_y += BODY_LINE_HEIGHT
                current_offset += len(paragraph) + 1  # +1 for newline

            total_y = current_y
        else:
            total_y = text_origin_y

        # Build anchor map for all stroke parents
        anchor_map: dict[CrdtId, tuple[float, float]] = {}

        # Collect all parent_ids from strokes
        parent_ids: set[CrdtId] = set()
        for block in blocks:
            if isinstance(block, SceneLineItemBlock):
                parent_ids.add(block.parent_id)

        # ROOT_LAYER uses page-center-relative coordinates
        root_layer_id = CrdtId(0, 11)
        anchor_map[root_layer_id] = (PAGE_CENTER_X, 0.0)

        for parent_id in parent_ids:
            if parent_id == root_layer_id:
                continue  # Already handled

            # Get anchor data from resolver (reuses CRDT ID -> char offset mapping)
            anchor = resolver.get_anchor(parent_id)

            # Compute page X: add PAGE_CENTER_X to text-relative anchor_x
            page_x = PAGE_CENTER_X + anchor.anchor_x

            # Compute visual Y using the pre-calculated char_to_y map
            if anchor.char_offset is None:
                page_y = text_origin_y
            elif anchor.char_offset == END_OF_DOC_ANCHOR_MARKER:
                page_y = total_y
            elif anchor.char_offset in char_to_y:
                page_y = char_to_y[anchor.char_offset]
            else:
                # Character offset not found - use end of text
                page_y = total_y

            # Add baseline offset: device anchors strokes to baseline, but
            # char_to_y stores top of text line
            page_y += STROKE_BASELINE_OFFSET

            anchor_map[parent_id] = (page_x, page_y)

        return anchor_map

    def _extract_strokes(self, blocks: list) -> list[SceneLineItemBlock]:
        """Extract all stroke blocks from the block list."""
        return [b for b in blocks if isinstance(b, SceneLineItemBlock)]

    def _extract_highlights(self, blocks: list) -> list[SceneGlyphItemBlock]:
        """Extract all highlight blocks from the block list."""
        return [b for b in blocks if isinstance(b, SceneGlyphItemBlock)]

    def _extract_text_with_styles(
        self, blocks: list
    ) -> tuple[str, float, float, float, dict[int, si.ParagraphStyle]]:
        """Extract text content, origin, text_width, and paragraph styles from RootTextBlock.

        Returns:
            Tuple of (text_content, origin_x, origin_y, text_width, paragraph_styles)
            paragraph_styles maps character offset to ParagraphStyle
        """
        for block in blocks:
            if isinstance(block, RootTextBlock):
                text_data = block.value

                # Get text origin position and width
                origin_x = text_data.pos_x if hasattr(text_data, "pos_x") else TEXT_ORIGIN_X
                origin_y = text_data.pos_y if hasattr(text_data, "pos_y") else TEXT_ORIGIN_Y
                text_width = text_data.width if hasattr(text_data, "width") else None

                # Extract text content from CRDT items and build CRDT ID -> char offset map
                text_parts = []
                crdt_to_char: dict[CrdtId, int] = {}
                char_offset = 0
                for item in text_data.items.sequence_items():
                    if hasattr(item, "value") and isinstance(item.value, str):
                        text = item.value
                        item_id = item.item_id
                        # Map each character's CRDT ID to its offset
                        for i in range(len(text)):
                            char_crdt_id = CrdtId(item_id.part1, item_id.part2 + i)
                            crdt_to_char[char_crdt_id] = char_offset + i
                        text_parts.append(text)
                        char_offset += len(text)

                # Extract paragraph styles using CRDT ID -> char offset mapping
                paragraph_styles: dict[int, si.ParagraphStyle] = {}
                if hasattr(text_data, "styles") and text_data.styles:
                    for style_crdt_id, lww_value in text_data.styles.items():
                        # Style CRDT IDs may have different author IDs (part1) than text items.
                        # For system-generated styles (part1=0), part2 is the character offset.
                        # For user-generated styles, look up in the crdt_to_char map.
                        if style_crdt_id.part1 == 0:
                            # System style - part2 is the character offset directly
                            actual_char_offset = style_crdt_id.part2
                        elif style_crdt_id in crdt_to_char:
                            # User style - look up in the map
                            actual_char_offset = crdt_to_char[style_crdt_id]
                        else:
                            # Unknown style CRDT ID - skip
                            continue
                        paragraph_styles[actual_char_offset] = lww_value.value

                return "".join(text_parts), origin_x, origin_y, text_width, paragraph_styles

        return "", TEXT_ORIGIN_X, TEXT_ORIGIN_Y, None, {}

    def _render_text(
        self,
        draw: ImageDraw.Draw,
        text: str,
        origin_x: float,
        origin_y: float,
        text_width: float | None,
        paragraph_styles: dict[int, si.ParagraphStyle],
    ) -> None:
        """Render text content to the image with proper formatting and word wrap.

        Args:
            draw: PIL ImageDraw object
            text: Full text content
            origin_x: Text origin X (text-relative, x=0 is page center)
            origin_y: Text origin Y (page coordinates)
            text_width: Text width from .rm file (for word wrapping), or None for default
            paragraph_styles: Map of character offset to paragraph style
        """
        if not text:
            return

        # Convert text-relative X to page coordinates
        page_x = PAGE_CENTER_X + origin_x

        # Use text_width from .rm file if available, otherwise calculate from page width
        if text_width is not None:
            max_text_width = text_width
        else:
            max_text_width = self.width - page_x - 50  # 50px right margin

        # Build list of (paragraph_text, style) by splitting on newlines
        # and determining style for each paragraph
        paragraphs = []
        current_offset = 0

        for line in text.split("\n"):
            # Determine style for this paragraph based on its starting offset
            style = paragraph_styles.get(current_offset, si.ParagraphStyle.BASIC)
            paragraphs.append((line, style))
            current_offset += len(line) + 1  # +1 for the newline

        # Render each paragraph with word wrapping
        current_y = origin_y
        prev_was_empty = False

        for line_text, style in paragraphs:
            # Determine font and line height based on style
            is_heading = style == si.ParagraphStyle.HEADING
            font = HEADING_FONT if is_heading else BODY_FONT
            line_height = HEADING_LINE_HEIGHT if is_heading else BODY_LINE_HEIGHT

            if line_text:
                # Word wrap the line
                wrapped_lines = self._wrap_text(line_text, max_text_width)
                for wrapped_line in wrapped_lines:
                    draw.text((page_x, current_y), wrapped_line, fill=TEXT_COLOR, font=font)
                    current_y += line_height
                prev_was_empty = False
            else:
                # Empty line - add inter-paragraph spacing
                # But avoid double-spacing for consecutive empty lines
                if not prev_was_empty:
                    current_y += BODY_LINE_HEIGHT
                prev_was_empty = True

    def _wrap_text(self, text: str, max_width: float) -> list[str]:
        """Wrap text to fit within max_width using the layout engine.

        Args:
            text: Text to wrap (single paragraph, no newlines)
            max_width: Maximum width in pixels

        Returns:
            List of wrapped lines
        """
        if not text:
            return [""]

        # Use the existing WordWrapLayoutEngine for consistent wrapping
        from rock_paper_sync.layout import WordWrapLayoutEngine

        engine = WordWrapLayoutEngine(use_font_metrics=True)
        line_breaks = engine.calculate_line_breaks(text, max_width)

        # Convert line breaks to list of strings
        lines = []
        for i, start in enumerate(line_breaks):
            if i + 1 < len(line_breaks):
                end = line_breaks[i + 1]
                # Strip trailing space from wrapped line
                lines.append(text[start:end].rstrip())
            else:
                lines.append(text[start:].rstrip())

        return lines if lines else [""]

    def _render_stroke(
        self,
        draw: ImageDraw.Draw,
        image: Image.Image,
        stroke: SceneLineItemBlock,
        anchor_map: dict[CrdtId, tuple[float, float]],
    ) -> None:
        """Render a single stroke to the image.

        Args:
            draw: PIL ImageDraw object
            image: PIL Image for alpha blending
            stroke: SceneLineItemBlock containing stroke data
            anchor_map: Map from parent_id to coordinate offset
        """
        if not stroke.item or not stroke.item.value:
            return

        line: si.Line = stroke.item.value

        if not line.points:
            return

        # Get coordinate offset based on parent_id
        parent_id = stroke.parent_id
        offset_x, offset_y = anchor_map.get(parent_id, (0.0, 0.0))

        # Get pen color
        color = get_pen_color(line.color)

        # Check if this is a highlighter stroke
        is_highlighter = line.tool in (si.Pen.HIGHLIGHTER_1, si.Pen.HIGHLIGHTER_2)

        if is_highlighter:
            # Draw highlighter as semi-transparent thick line
            self._render_highlighter_stroke(image, line, offset_x, offset_y)
        else:
            # Draw regular stroke
            self._render_regular_stroke(draw, line, offset_x, offset_y, color)

    def _render_regular_stroke(
        self,
        draw: ImageDraw.Draw,
        line: si.Line,
        offset_x: float,
        offset_y: float,
        color: tuple[int, int, int],
    ) -> None:
        """Render a regular (non-highlighter) stroke."""
        points = [(p.x + offset_x, p.y + offset_y) for p in line.points]

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
        offset_x: float,
        offset_y: float,
    ) -> None:
        """Render a highlighter stroke as semi-transparent yellow."""
        points = [(p.x + offset_x, p.y + offset_y) for p in line.points]

        if len(points) < 2:
            return

        # Get highlighter color with alpha
        highlight_rgba = get_highlight_rgba()

        # Create overlay for alpha blending
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        # Use thick line width for highlighter effect
        width = max(15, int(line.points[0].width * 2))

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
            Highlight rectangles are in text coordinates (x=0 is page center).
            To convert to page coordinates: page_x = PAGE_CENTER_X + rect.x
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
            # Convert text coordinates to page coordinates
            x1 = PAGE_CENTER_X + rect.x
            y1 = rect.y  # Y is already in page coordinates
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
