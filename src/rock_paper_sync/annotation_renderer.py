"""Render device annotations to markdown format (M5).

This module handles converting annotations from reMarkable device format
into markdown representation for Obsidian. This is the core of pull sync -
annotations made on device appear in the markdown files.

Rendering strategies:
- Highlights: `==text==` (Obsidian highlight syntax)
- Strokes: Footnotes `[^n]: OCR text` (OCR'd handwriting)
- Orphans: HTML comment `<!-- N orphaned annotations -->` (minimal format)
"""

import logging
import re
from dataclasses import dataclass, field

from rock_paper_sync.annotations.document_model import DocumentAnnotation, DocumentModel

logger = logging.getLogger("rock_paper_sync.annotation_renderer")


@dataclass
class RenderConfig:
    """Configuration for annotation rendering."""

    # Highlight style: 'obsidian' (==text==) or 'html_comment'
    highlight_style: str = "obsidian"
    # Stroke style: 'footnote' (default), 'inline', or 'comment'
    stroke_style: str = "footnote"
    # Where to put orphan comment: 'top' or 'bottom'
    orphan_comment_location: str = "top"
    # Include OCR confidence in footnotes
    include_ocr_confidence: bool = False


@dataclass
class RenderResult:
    """Result of annotation rendering."""

    # Rendered markdown content
    content: str
    # Number of highlights rendered
    highlights_rendered: int = 0
    # Number of strokes rendered
    strokes_rendered: int = 0
    # Number of orphaned annotations
    orphans_count: int = 0
    # Details about orphaned annotations
    orphan_details: list[str] = field(default_factory=list)


class AnnotationRenderer:
    """Renders device annotations to markdown format.

    This class takes annotations from a DocumentModel (extracted from device .rm files)
    and renders them into markdown format suitable for Obsidian.
    """

    def __init__(self, config: RenderConfig | None = None) -> None:
        """Initialize annotation renderer.

        Args:
            config: Optional render configuration. Defaults to standard settings.
        """
        self.config = config or RenderConfig()
        self._footnote_counter = 0

    def render(
        self,
        markdown_content: str,
        document_model: DocumentModel,
        orphaned_annotations: list[DocumentAnnotation] | None = None,
    ) -> RenderResult:
        """Render all annotations into markdown content.

        Args:
            markdown_content: Original markdown content
            document_model: DocumentModel with annotations to render
            orphaned_annotations: List of annotations that couldn't be anchored

        Returns:
            RenderResult with rendered content and statistics
        """
        self._footnote_counter = 0
        result = RenderResult(content=markdown_content)

        # Collect annotations by type
        highlights = [a for a in document_model.annotations if a.annotation_type == "highlight"]
        strokes = [a for a in document_model.annotations if a.annotation_type == "stroke"]

        # Render highlights (inline)
        if highlights:
            result.content = self.render_highlights(result.content, highlights)
            result.highlights_rendered = len(highlights)

        # Render strokes (as footnotes)
        if strokes:
            result.content = self.render_strokes(result.content, strokes)
            result.strokes_rendered = len(strokes)

        # Add orphan comment if needed
        if orphaned_annotations:
            result.content = self.render_orphan_comment(result.content, orphaned_annotations)
            result.orphans_count = len(orphaned_annotations)
            result.orphan_details = [
                f"{a.annotation_type}: {a.anchor_context.text_content[:30] if a.anchor_context else 'unknown'}..."
                for a in orphaned_annotations
            ]

        return result

    def render_highlights(
        self,
        content: str,
        highlights: list[DocumentAnnotation],
    ) -> str:
        """Render highlight annotations into markdown content.

        Highlights are rendered as ==text== (Obsidian syntax) or HTML comments
        depending on configuration.

        Args:
            content: Markdown content to modify
            highlights: List of highlight annotations

        Returns:
            Modified markdown content with highlight markers
        """
        if not highlights:
            return content

        # Sort highlights by position (reverse order to preserve offsets)
        # Use find_in() to get position since AnchorContext doesn't store char_offset
        sorted_highlights = sorted(
            highlights,
            key=lambda h: h.anchor_context.find_in(content) if h.anchor_context else 0,
            reverse=True,
        )

        result = content
        for highlight in sorted_highlights:
            if not highlight.anchor_context:
                continue

            # Find the text to highlight
            anchor_text = highlight.anchor_context.text_content
            if not anchor_text:
                continue

            # Check if this text already has highlight markers
            if f"=={anchor_text}==" in result:
                logger.debug(f"Text already highlighted: {anchor_text[:30]}...")
                continue

            # Find and replace the text with highlighted version
            # Use word boundaries to avoid partial matches
            escaped_text = re.escape(anchor_text)
            pattern = f"(?<!==){escaped_text}(?!==)"

            if self.config.highlight_style == "obsidian":
                replacement = f"=={anchor_text}=="
            else:
                replacement = f"<!-- HL -->{anchor_text}<!-- /HL -->"

            # Replace first occurrence only (in case of duplicates)
            result = re.sub(pattern, replacement, result, count=1)

            logger.debug(f"Rendered highlight: {anchor_text[:30]}...")

        return result

    def render_strokes(
        self,
        content: str,
        strokes: list[DocumentAnnotation],
    ) -> str:
        """Render stroke annotations into markdown content.

        Strokes are rendered as footnotes with OCR'd text by default.

        Args:
            content: Markdown content to modify
            strokes: List of stroke annotations

        Returns:
            Modified markdown content with stroke footnotes
        """
        if not strokes:
            return content

        # Group strokes by anchor location
        strokes_by_anchor: dict[int, list[DocumentAnnotation]] = {}
        for stroke in strokes:
            if stroke.anchor_context:
                # Use find_in() to get position since AnchorContext doesn't store char_offset
                anchor_pos = stroke.anchor_context.find_in(content)
                if anchor_pos >= 0:  # Only include if found
                    if anchor_pos not in strokes_by_anchor:
                        strokes_by_anchor[anchor_pos] = []
                    strokes_by_anchor[anchor_pos].append(stroke)

        if not strokes_by_anchor:
            return content

        result = content
        footnotes: list[str] = []

        # Sort by position (reverse order to preserve offsets)
        for anchor_pos in sorted(strokes_by_anchor.keys(), reverse=True):
            stroke_group = strokes_by_anchor[anchor_pos]

            for stroke in stroke_group:
                # Get OCR text if available
                ocr_text = self._get_stroke_ocr_text(stroke)
                if not ocr_text:
                    ocr_text = "[handwriting]"

                # Generate footnote
                self._footnote_counter += 1
                footnote_id = f"stroke-{self._footnote_counter}"

                # Insert footnote reference at anchor position
                if self.config.stroke_style == "footnote":
                    ref_marker = f"[^{footnote_id}]"

                    # Find the end of the paragraph/sentence for insertion
                    anchor_text = (
                        stroke.anchor_context.text_content if stroke.anchor_context else ""
                    )
                    if anchor_text:
                        # Insert after the anchor text
                        escaped_text = re.escape(anchor_text)
                        result = re.sub(
                            f"({escaped_text})",
                            f"\\1{ref_marker}",
                            result,
                            count=1,
                        )
                    else:
                        # Fallback: insert at position
                        result = result[:anchor_pos] + ref_marker + result[anchor_pos:]

                    # Add footnote definition
                    footnote_def = f"[^{footnote_id}]: {ocr_text}"
                    if self.config.include_ocr_confidence and stroke.stroke_data:
                        confidence = getattr(stroke.stroke_data, "ocr_confidence", None)
                        if confidence:
                            footnote_def += f" (confidence: {confidence:.0%})"
                    footnotes.append(footnote_def)

                elif self.config.stroke_style == "inline":
                    # Inline rendering
                    result = result[:anchor_pos] + f" *[{ocr_text}]* " + result[anchor_pos:]

                elif self.config.stroke_style == "comment":
                    # HTML comment rendering
                    result = (
                        result[:anchor_pos] + f"<!-- stroke: {ocr_text} -->" + result[anchor_pos:]
                    )

                logger.debug(f"Rendered stroke: {ocr_text[:30]}...")

        # Append footnotes at end of document
        if footnotes:
            result = result.rstrip() + "\n\n" + "\n".join(footnotes) + "\n"

        return result

    def render_orphan_comment(
        self,
        content: str,
        orphaned_annotations: list[DocumentAnnotation],
    ) -> str:
        """Add minimal HTML comment for orphaned annotations.

        Per product decision, this is a minimal comment at the top of the file:
        `<!-- N orphaned annotations preserved in device file -->`

        Args:
            content: Markdown content to modify
            orphaned_annotations: List of orphaned annotations

        Returns:
            Modified markdown content with orphan comment
        """
        if not orphaned_annotations:
            return content

        count = len(orphaned_annotations)
        comment = f"<!-- {count} orphaned annotation{'s' if count != 1 else ''} preserved in device file -->\n\n"

        if self.config.orphan_comment_location == "top":
            # Check if there's already an orphan comment
            if "orphaned annotation" in content[:100]:
                # Update existing comment
                content = re.sub(
                    r"<!-- \d+ orphaned annotations? preserved in device file -->\n\n",
                    comment,
                    content,
                )
            else:
                content = comment + content
        else:
            content = content.rstrip() + "\n\n" + comment

        return content

    def _get_stroke_ocr_text(self, stroke: DocumentAnnotation) -> str | None:
        """Extract OCR text from a stroke annotation.

        Args:
            stroke: Stroke annotation

        Returns:
            OCR text if available, None otherwise
        """
        if stroke.stroke_data:
            return getattr(stroke.stroke_data, "ocr_text", None)
        return None


def render_annotations_to_markdown(
    markdown_content: str,
    document_model: DocumentModel,
    orphaned_annotations: list[DocumentAnnotation] | None = None,
    config: RenderConfig | None = None,
) -> RenderResult:
    """Convenience function to render annotations to markdown.

    Args:
        markdown_content: Original markdown content
        document_model: DocumentModel with annotations
        orphaned_annotations: Optional list of orphaned annotations
        config: Optional render configuration

    Returns:
        RenderResult with rendered content and statistics
    """
    renderer = AnnotationRenderer(config)
    return renderer.render(markdown_content, document_model, orphaned_annotations)
