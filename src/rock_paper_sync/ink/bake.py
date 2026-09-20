"""Bake drawing clusters into SVG attachments.

Renders a stroke cluster to an SVG image and produces a ``BakedOverlay``
for placement into the next PDF render.

Attachment naming: ``{stem}-drawing-p{page}-{cluster_id[:8]}.svg``
Attachment folder: from ``.obsidian/app.json`` ``attachmentFolderPath``
(vault root if unreadable).

Import restrictions (enforced by import-linter):
    Must NOT import ``weasyprint``, ``fitz``, ``pymupdf``,
    ``rock_paper_sync.sync_v3``, or ``rock_paper_sync.rm_cloud_sync``.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from rock_paper_sync.device_profile import CanvasTransform, device_to_pdf
from rock_paper_sync.ink.clusters import InkCluster
from rock_paper_sync.ink.dispositions import InkAnchor
from rock_paper_sync.render.wordmap import BakedOverlay, Rect

__all__ = [
    "bake_cluster",
    "get_attachment_folder",
    "render_cluster_to_png",
]

logger = logging.getLogger(__name__)

# Default stroke width in PDF points
_DEFAULT_STROKE_WIDTH_PT = 1.0
# Scale factor for pen thickness (device units -> PDF points)
_PEN_THICKNESS_SCALE = 0.5


def bake_cluster(
    cluster: InkCluster,
    transform: CanvasTransform,
    anchor: InkAnchor,
    anchor_rect: Rect | None,
    stem: str,
) -> tuple[str, bytes, BakedOverlay]:
    """Render a cluster to SVG and produce a BakedOverlay.

    Args:
        cluster: The cluster to bake.
        transform: Canvas transform for coordinate conversion.
        anchor: The anchor for this overlay.
        anchor_rect: The paragraph box this drawing is near (PDF points).
        stem: Markdown file stem (without extension) for naming.

    Returns:
        Tuple of (attachment_filename, svg_bytes, BakedOverlay).
    """
    page = cluster.redir if cluster.redir is not None else 0
    attachment_name = f"{stem}-drawing-p{page}-{cluster.cluster_id[:8]}.svg"

    # Transform all stroke points from device to PDF coordinates
    all_points_pdf: list[list[tuple[float, float, float]]] = []
    for cs in cluster.strokes:
        stroke_pts: list[tuple[float, float, float]] = []
        for pt in cs.stroke.points:
            px, py = device_to_pdf(transform, pt.x, pt.y)
            pt_w = getattr(pt, "width", None)
            width = (
                float(pt_w) * _PEN_THICKNESS_SCALE if pt_w is not None else _DEFAULT_STROKE_WIDTH_PT
            )
            stroke_pts.append((px, py, width))
        all_points_pdf.append(stroke_pts)

    # Compute bounding box in PDF points
    all_x = [p[0] for pts in all_points_pdf for p in pts]
    all_y = [p[1] for pts in all_points_pdf for p in pts]

    if not all_x:
        # Degenerate cluster with no points
        svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'
        offset = Rect(x0=0, y0=0, x1=1, y1=1)
        overlay = BakedOverlay(
            anchor=anchor.ref if anchor.kind == "paragraph" else "document_end",
            offset=offset,
            svg=svg_bytes,
        )
        return attachment_name, svg_bytes, overlay

    margin = 2.0  # small padding
    vb_x0 = min(all_x) - margin
    vb_y0 = min(all_y) - margin
    vb_x1 = max(all_x) + margin
    vb_y1 = max(all_y) + margin
    vb_w = vb_x1 - vb_x0
    vb_h = vb_y1 - vb_y0

    # Build SVG
    svg = ET.Element("svg")
    svg.set("xmlns", "http://www.w3.org/2000/svg")
    svg.set("viewBox", f"{vb_x0:.2f} {vb_y0:.2f} {vb_w:.2f} {vb_h:.2f}")
    svg.set("width", f"{vb_w:.1f}")
    svg.set("height", f"{vb_h:.1f}")

    for stroke_pts in all_points_pdf:
        if len(stroke_pts) < 2:
            continue
        # Build path data
        parts = [f"M{stroke_pts[0][0]:.2f},{stroke_pts[0][1]:.2f}"]
        for px, py, _ in stroke_pts[1:]:
            parts.append(f"L{px:.2f},{py:.2f}")

        path = ET.SubElement(svg, "path")
        path.set("d", " ".join(parts))
        # Use median width for the stroke
        widths = [p[2] for p in stroke_pts]
        median_w = sorted(widths)[len(widths) // 2]
        path.set("stroke", "black")
        path.set("stroke-width", f"{median_w:.2f}")
        path.set("fill", "none")
        path.set("stroke-linecap", "round")
        path.set("stroke-linejoin", "round")

    svg_bytes = ET.tostring(svg, encoding="unicode").encode("utf-8")

    # Compute offset relative to anchor rect
    if anchor_rect is not None:
        offset = Rect(
            x0=vb_x0 - anchor_rect.x0,
            y0=vb_y0 - anchor_rect.y0,
            x1=vb_x1 - anchor_rect.x0,
            y1=vb_y1 - anchor_rect.y0,
        )
    else:
        offset = Rect(x0=vb_x0, y0=vb_y0, x1=vb_x1, y1=vb_y1)

    anchor_str = anchor.ref if anchor.kind == "paragraph" else "document_end"
    overlay = BakedOverlay(anchor=anchor_str, offset=offset, svg=svg_bytes)

    return attachment_name, svg_bytes, overlay


def get_attachment_folder(vault_path: Path) -> Path:
    """Get the Obsidian attachment folder for a vault.

    Reads ``.obsidian/app.json`` ``attachmentFolderPath``.
    Falls back to vault root if unreadable.

    Args:
        vault_path: Path to the vault root.

    Returns:
        Absolute path to the attachment folder.
    """
    app_json = vault_path / ".obsidian" / "app.json"
    try:
        data = json.loads(app_json.read_text(encoding="utf-8"))
        folder = data.get("attachmentFolderPath", "")
        if folder:
            if folder.startswith("./"):
                # Relative to note — for simplicity, use vault root
                return vault_path
            return vault_path / folder
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return vault_path


def render_cluster_to_png(
    cluster: InkCluster,
    transform: CanvasTransform,
    scale: float = 2.0,
) -> bytes:
    """Render a cluster to PNG bytes for OCR.

    Uses PIL to draw the strokes at a fixed scale.

    Args:
        cluster: The cluster to render.
        transform: Canvas transform for coordinate conversion.
        scale: Pixels per PDF point.

    Returns:
        PNG image bytes.
    """
    from io import BytesIO

    from PIL import Image, ImageDraw

    # Transform points to PDF coordinates
    all_points: list[list[tuple[float, float]]] = []
    for cs in cluster.strokes:
        pts = []
        for pt in cs.stroke.points:
            px, py = device_to_pdf(transform, pt.x, pt.y)
            pts.append((px, py))
        all_points.append(pts)

    if not all_points or not any(all_points):
        # Empty cluster -> 1x1 white image
        img = Image.new("L", (1, 1), 255)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # Compute bounds
    all_x = [p[0] for pts in all_points for p in pts]
    all_y = [p[1] for pts in all_points for p in pts]
    margin = 5.0
    x0 = min(all_x) - margin
    y0 = min(all_y) - margin
    x1 = max(all_x) + margin
    y1 = max(all_y) + margin

    w = int((x1 - x0) * scale) + 1
    h = int((y1 - y0) * scale) + 1
    w = max(w, 1)
    h = max(h, 1)

    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)

    for pts in all_points:
        if len(pts) < 2:
            continue
        scaled = [((p[0] - x0) * scale, (p[1] - y0) * scale) for p in pts]
        draw.line(scaled, fill=0, width=max(1, int(2 * scale)))

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
