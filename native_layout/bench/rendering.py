"""Bench render pipeline: parse -> generate -> render, plus diagnostic overlay.

All layout numbers come from the production engine (DeviceGeometry,
LayoutContext, WordWrapLayoutEngine via tools.rmlib.compute_text_line_layout).
The only constants defined in this module are presentation-only overlay
colors and alpha values.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import rmscene
from PIL import Image, ImageDraw

from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.coordinates import PAGE_CENTER_X
from rock_paper_sync.generator import RemarkableGenerator
from rock_paper_sync.parser import parse_markdown_file
from rock_paper_sync.rm_file_extractor import RmFileExtractor

if TYPE_CHECKING:
    from native_layout.layout.device import DeviceGeometry

# --- Presentation-only overlay constants (colors/alpha; NOT layout math) ---
GRID_COLOR = (120, 170, 255, 90)  # line grid
BOUNDS_COLOR = (0, 130, 0, 160)  # text-area bounds
WRAP_COLOR = (230, 60, 60, 220)  # wrap-point ticks
PAGE_BREAK_COLOR = (200, 0, 200, 200)  # page-break indicator
ANCHOR_COLOR = (255, 140, 0, 220)  # TreeNodeBlock anchor markers
WRAP_TICK_LEN = 18  # pixels, marker length (presentation)
ANCHOR_TICK_LEN = 40  # pixels, marker length (presentation)


def _rmlib() -> Any:
    """Import native_layout.tools.rmlib.renderer lazily."""
    from native_layout.tools.rmlib import renderer as rmlib_renderer

    return rmlib_renderer


@dataclass
class RenderedPage:
    """One rendered page with its diagnostics."""

    index: int  # 0-based page index
    image: Image.Image
    line_count: int  # visual lines from the production layout engine
    rm_bytes: bytes


@dataclass
class RenderResult:
    """Result of rendering a markdown document."""

    pages: list[RenderedPage]
    elapsed_seconds: float


def render_markdown(md_path: Path, overlay: bool = False) -> RenderResult:
    """Parse markdown, generate .rm pages, render each page to a PNG image.

    Bench composes production components only: parser -> RemarkableGenerator
    (pagination + .rm bytes) -> tools.rmlib RmRenderer (pixels).
    """
    rmlib_renderer = _rmlib()
    start = time.perf_counter()

    md_doc = parse_markdown_file(md_path)
    generator = RemarkableGenerator(LayoutConfig())
    doc = generator.generate_document(md_doc)

    renderer = rmlib_renderer.RmRenderer()
    pages: list[RenderedPage] = []
    for index, page in enumerate(doc.pages):
        rm_bytes = generator.generate_rm_file(page)
        image = renderer.render_bytes(rm_bytes)
        line_count = _count_lines(rm_bytes, generator.geometry)
        if overlay:
            image = draw_overlay(image, rm_bytes, generator.geometry)
        pages.append(
            RenderedPage(index=index, image=image, line_count=line_count, rm_bytes=rm_bytes)
        )

    return RenderResult(pages=pages, elapsed_seconds=time.perf_counter() - start)


def _layout_lines(rm_bytes: bytes, geometry: DeviceGeometry) -> list[tuple[int, int, float, float]]:
    """Visual lines (start, end, doc_x, doc_y) from the production engine."""
    rmlib_renderer = _rmlib()
    blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    extractor = RmFileExtractor.from_blocks(blocks)
    layout_ctx = extractor.get_layout_context(geometry)
    return rmlib_renderer.compute_text_line_layout(layout_ctx)


def _count_lines(rm_bytes: bytes, geometry: DeviceGeometry) -> int:
    return len(_layout_lines(rm_bytes, geometry))


def draw_overlay(image: Image.Image, rm_bytes: bytes, geometry: DeviceGeometry) -> Image.Image:
    """Draw layout diagnostics on top of a rendered page.

    Draws: line grid at DeviceGeometry.line_height pitch from text_pos_y,
    text-area bounds, wrap points (production line breaks), the page-break
    line, and TreeNodeBlock anchor markers at anchor_origin_x.
    """
    base = image.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    page_w, page_h = base.size
    text_left = PAGE_CENTER_X + geometry.text_pos_x
    text_right = text_left + geometry.text_width

    # 1. Horizontal line grid at line_height pitch from text_pos_y
    y = geometry.text_pos_y
    while y < page_h:
        draw.line([(text_left, y), (text_right, y)], fill=GRID_COLOR, width=1)
        y += geometry.line_height

    # 2. Text-area bounds (vertical edges at text_pos_x / text_pos_x+text_width)
    draw.line([(text_left, 0), (text_left, page_h)], fill=BOUNDS_COLOR, width=2)
    draw.line([(text_right, 0), (text_right, page_h)], fill=BOUNDS_COLOR, width=2)

    # 3. Wrap points: start of each visual line from the production engine
    for _start, _end, doc_x, doc_y in _layout_lines(rm_bytes, geometry):
        x = PAGE_CENTER_X + doc_x
        draw.line([(x, doc_y), (x, doc_y + WRAP_TICK_LEN)], fill=WRAP_COLOR, width=3)

    # 4. Page-break indicator: bottom of the last engine line slot
    break_y = geometry.text_pos_y + geometry.lines_per_page * geometry.line_height
    if break_y < page_h:
        draw.line([(0, break_y), (page_w, break_y)], fill=PAGE_BREAK_COLOR, width=2)

    # 5. TreeNodeBlock anchor markers at anchor_origin_x (labeled with part2)
    for anchor_x, part2, anchor_y in _anchor_markers(rm_bytes, geometry):
        x = PAGE_CENTER_X + anchor_x
        y0 = anchor_y if anchor_y is not None else geometry.text_pos_y
        draw.line([(x, y0), (x, y0 + ANCHOR_TICK_LEN)], fill=ANCHOR_COLOR, width=3)
        draw.text((x + 4, y0), str(part2), fill=ANCHOR_COLOR)

    return Image.alpha_composite(base, layer).convert("RGB")


def _anchor_markers(
    rm_bytes: bytes, geometry: DeviceGeometry
) -> list[tuple[float, int, float | None]]:
    """Extract (anchor_origin_x, node_id.part2, resolved_y) per TreeNodeBlock.

    anchor_origin_x comes straight from the .rm TreeNodeBlock; the y (when
    resolvable) comes from the production AnchorResolver.
    """
    from rock_paper_sync.coordinates import AnchorResolver

    blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    extractor = RmFileExtractor.from_blocks(blocks)
    resolver = AnchorResolver.from_extractor(extractor)

    markers: list[tuple[float, int, float | None]] = []
    for block in blocks:
        if type(block).__name__ != "TreeNodeBlock":
            continue
        group = getattr(block, "group", None)
        if group is None:
            continue
        aox = getattr(group, "anchor_origin_x", None)
        if aox is None or aox.value is None:
            continue
        anchor = resolver.get_anchor(group.node_id)
        anchor_y = anchor.y if anchor is not None else None
        markers.append((float(aox.value), group.node_id.part2, anchor_y))
    return markers
