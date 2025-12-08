"""Debug data capture for annotation debugging.

Captures detailed debugging information that is NOT committed to git.
All output goes to debug_output/ directories which are gitignored.

This module provides tools to capture:
- Before/after .rm file comparisons
- Anchor calculations and validation results
- Trace events from annotation processing
- Delta analysis between old and new text

Usage:
    from tools.rmlib.capture import DebugCapture

    # Create a capture session
    with DebugCapture("anchor_bug_investigation") as capture:
        # Save input state
        capture.save_input_rm_files(rm_files)
        capture.save_input_markdown(markdown_path)

        # Run sync operation with tracing enabled
        with AnnotationTracer() as tracer:
            result = run_sync(...)

            # Save trace and output
            capture.save_trace(tracer)
            capture.save_output_rm_files(result.rm_files)
            capture.save_validation_results(validate_output(...))

        # Generate comparison report
        capture.generate_report()
"""

from __future__ import annotations

import io
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import rmscene
from rmscene.scene_stream import RootTextBlock

# Debug output directory - gitignored
DEBUG_OUTPUT_DIR = Path(__file__).parent.parent.parent / "debug_output"


@dataclass
class AnchorInfo:
    """Information about a TreeNodeBlock anchor."""

    node_id: str
    anchor_offset: int | None
    page_text_len: int
    is_valid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "anchor_offset": self.anchor_offset,
            "page_text_len": self.page_text_len,
            "is_valid": self.is_valid,
        }


@dataclass
class PageAnalysis:
    """Analysis of a single .rm file/page."""

    page_index: int
    file_name: str
    page_text_len: int
    stroke_count: int
    tree_node_count: int
    anchors: list[AnchorInfo] = field(default_factory=list)
    text_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "file_name": self.file_name,
            "page_text_len": self.page_text_len,
            "stroke_count": self.stroke_count,
            "tree_node_count": self.tree_node_count,
            "anchors": [a.to_dict() for a in self.anchors],
            "text_preview": self.text_preview,
        }


@dataclass
class DeltaAnalysis:
    """Analysis of text length changes between input and output."""

    page_index: int
    old_text_len: int
    new_text_len: int
    delta: int
    problematic_anchors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "old_text_len": self.old_text_len,
            "new_text_len": self.new_text_len,
            "delta": self.delta,
            "problematic_anchors": self.problematic_anchors,
        }


class DebugCapture:
    """Captures debugging data for annotation issues.

    All output is saved to debug_output/{session_name}/ which is gitignored.

    Usage:
        with DebugCapture("my_debug_session") as capture:
            capture.save_input_rm_files(rm_files)
            # ... run operations ...
            capture.generate_report()
    """

    def __init__(self, session_name: str | None = None) -> None:
        """Initialize debug capture session.

        Args:
            session_name: Name for this debug session.
                         If not provided, uses timestamp.
        """
        if session_name is None:
            session_name = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.session_name = session_name
        self.output_dir = DEBUG_OUTPUT_DIR / session_name
        self.input_analysis: list[PageAnalysis] = []
        self.output_analysis: list[PageAnalysis] = []
        self.delta_analysis: list[DeltaAnalysis] = []
        self.trace_events: list[dict[str, Any]] = []
        self.validation_results: dict[str, Any] = {}
        self.metadata: dict[str, Any] = {
            "session_name": session_name,
            "created_at": datetime.now().isoformat(),
        }

    def __enter__(self) -> DebugCapture:
        """Enter context manager - create output directory."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *args: object) -> None:
        """Exit context manager - save final state."""
        self._save_metadata()

    def _analyze_rm_bytes(self, rm_bytes: bytes, page_index: int, file_name: str) -> PageAnalysis:
        """Analyze a single .rm file's contents."""
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))

        # Get page text from RootTextBlock
        page_text_len = 0
        text_preview = ""
        for block in blocks:
            if isinstance(block, RootTextBlock):
                if hasattr(block, "value") and hasattr(block.value, "items"):
                    text_parts = []
                    for item_val in block.value.items.values():
                        if isinstance(item_val, str):
                            text_parts.append(item_val)
                    text = "".join(text_parts)
                    page_text_len = len(text)
                    text_preview = text[:200].replace("\n", "\\n")
                break

        # Count strokes and tree nodes
        strokes = [b for b in blocks if "Line" in type(b).__name__]
        tree_nodes = [
            b
            for b in blocks
            if type(b).__name__ == "TreeNodeBlock"
            and hasattr(b, "group")
            and b.group
            and hasattr(b.group, "node_id")
            and b.group.node_id
            and b.group.node_id.part1 == 2  # User-created
        ]

        # Extract anchor info
        anchors = []
        for tn in tree_nodes:
            g = tn.group
            if hasattr(g, "anchor_id") and g.anchor_id:
                anchor_offset = g.anchor_id.value.part2 if g.anchor_id.value else None
                is_valid = anchor_offset is not None and 0 <= anchor_offset <= page_text_len
                anchors.append(
                    AnchorInfo(
                        node_id=str(g.node_id),
                        anchor_offset=anchor_offset,
                        page_text_len=page_text_len,
                        is_valid=is_valid,
                    )
                )

        return PageAnalysis(
            page_index=page_index,
            file_name=file_name,
            page_text_len=page_text_len,
            stroke_count=len(strokes),
            tree_node_count=len(tree_nodes),
            anchors=anchors,
            text_preview=text_preview,
        )

    def save_input_rm_files(self, rm_files: list[Path]) -> None:
        """Save and analyze input .rm files.

        Args:
            rm_files: List of paths to input .rm files
        """
        input_dir = self.output_dir / "input_rm_files"
        input_dir.mkdir(exist_ok=True)

        self.input_analysis = []
        for i, rm_file in enumerate(sorted(rm_files)):
            # Copy file
            dest = input_dir / rm_file.name
            shutil.copy(rm_file, dest)

            # Analyze
            rm_bytes = rm_file.read_bytes()
            analysis = self._analyze_rm_bytes(rm_bytes, i, rm_file.name)
            self.input_analysis.append(analysis)

        # Save analysis
        analysis_path = self.output_dir / "input_analysis.json"
        analysis_path.write_text(
            json.dumps([a.to_dict() for a in self.input_analysis], indent=2)
        )

    def save_input_rm_bytes(self, rm_data: dict[str, bytes]) -> None:
        """Save and analyze input .rm data from bytes.

        Args:
            rm_data: Dict mapping page_uuid -> .rm bytes
        """
        input_dir = self.output_dir / "input_rm_files"
        input_dir.mkdir(exist_ok=True)

        self.input_analysis = []
        for i, (page_uuid, rm_bytes) in enumerate(sorted(rm_data.items())):
            # Save file
            dest = input_dir / f"{page_uuid}.rm"
            dest.write_bytes(rm_bytes)

            # Analyze
            analysis = self._analyze_rm_bytes(rm_bytes, i, f"{page_uuid}.rm")
            self.input_analysis.append(analysis)

        # Save analysis
        analysis_path = self.output_dir / "input_analysis.json"
        analysis_path.write_text(
            json.dumps([a.to_dict() for a in self.input_analysis], indent=2)
        )

    def save_output_rm_bytes(self, rm_data: dict[str, bytes]) -> None:
        """Save and analyze output .rm data.

        Args:
            rm_data: Dict mapping page_uuid -> .rm bytes
        """
        output_dir = self.output_dir / "output_rm_files"
        output_dir.mkdir(exist_ok=True)

        self.output_analysis = []
        for i, (page_uuid, rm_bytes) in enumerate(sorted(rm_data.items())):
            # Save file
            dest = output_dir / f"{page_uuid}.rm"
            dest.write_bytes(rm_bytes)

            # Analyze
            analysis = self._analyze_rm_bytes(rm_bytes, i, f"{page_uuid}.rm")
            self.output_analysis.append(analysis)

        # Save analysis
        analysis_path = self.output_dir / "output_analysis.json"
        analysis_path.write_text(
            json.dumps([a.to_dict() for a in self.output_analysis], indent=2)
        )

        # Compute delta analysis if we have input
        if self.input_analysis:
            self._compute_delta_analysis()

    def save_input_markdown(self, markdown_path: Path) -> None:
        """Save a copy of the input markdown file.

        Args:
            markdown_path: Path to markdown file
        """
        dest = self.output_dir / "input_markdown.md"
        shutil.copy(markdown_path, dest)
        self.metadata["input_markdown"] = markdown_path.name

    def save_input_markdown_content(self, content: str, name: str = "input.md") -> None:
        """Save markdown content directly.

        Args:
            content: Markdown content string
            name: Name for the file
        """
        dest = self.output_dir / name
        dest.write_text(content)
        self.metadata["input_markdown"] = name

    def save_trace(self, tracer: Any) -> None:
        """Save trace events from an AnnotationTracer.

        Args:
            tracer: AnnotationTracer instance with captured events
        """
        if hasattr(tracer, "events"):
            self.trace_events = [e.to_dict() for e in tracer.events]

        trace_path = self.output_dir / "trace.json"
        trace_path.write_text(json.dumps(self.trace_events, indent=2))

        # Also save human-readable report
        if hasattr(tracer, "report"):
            report_path = self.output_dir / "trace_report.txt"
            report_path.write_text(tracer.report("text"))

    def save_validation_results(self, results: dict[str, Any]) -> None:
        """Save validation results.

        Args:
            results: Validation results dict
        """
        self.validation_results = results
        path = self.output_dir / "validation_results.json"
        path.write_text(json.dumps(results, indent=2))

    def _compute_delta_analysis(self) -> None:
        """Compute delta analysis between input and output."""
        self.delta_analysis = []

        # Match by page index (may not be correct if pages were reordered)
        for i, (inp, out) in enumerate(
            zip(self.input_analysis, self.output_analysis, strict=False)
        ):
            delta = out.page_text_len - inp.page_text_len
            problematic = []

            # Check if any input anchors would be invalid after delta
            for anchor in inp.anchors:
                if anchor.anchor_offset is not None and delta != 0:
                    # If anchor was adjusted by delta, would it be valid?
                    adjusted = anchor.anchor_offset + delta
                    if adjusted > out.page_text_len or adjusted < 0:
                        problematic.append(
                            {
                                "node_id": anchor.node_id,
                                "original_anchor": anchor.anchor_offset,
                                "adjusted_anchor": adjusted,
                                "new_page_text_len": out.page_text_len,
                                "issue": "adjusted anchor exceeds page text length",
                            }
                        )

            self.delta_analysis.append(
                DeltaAnalysis(
                    page_index=i,
                    old_text_len=inp.page_text_len,
                    new_text_len=out.page_text_len,
                    delta=delta,
                    problematic_anchors=problematic,
                )
            )

        # Save delta analysis
        path = self.output_dir / "delta_analysis.json"
        path.write_text(
            json.dumps([d.to_dict() for d in self.delta_analysis], indent=2)
        )

    def _save_metadata(self) -> None:
        """Save session metadata."""
        self.metadata["completed_at"] = datetime.now().isoformat()
        self.metadata["input_pages"] = len(self.input_analysis)
        self.metadata["output_pages"] = len(self.output_analysis)

        # Count invalid anchors
        invalid_input = sum(
            1 for a in self.input_analysis for anc in a.anchors if not anc.is_valid
        )
        invalid_output = sum(
            1 for a in self.output_analysis for anc in a.anchors if not anc.is_valid
        )
        self.metadata["invalid_input_anchors"] = invalid_input
        self.metadata["invalid_output_anchors"] = invalid_output

        path = self.output_dir / "metadata.json"
        path.write_text(json.dumps(self.metadata, indent=2))

    def generate_report(self) -> str:
        """Generate a human-readable summary report.

        Returns:
            Report string
        """
        lines = []
        lines.append("=" * 70)
        lines.append(f"DEBUG CAPTURE REPORT: {self.session_name}")
        lines.append("=" * 70)
        lines.append(f"Output directory: {self.output_dir}")
        lines.append("")

        # Input summary
        if self.input_analysis:
            lines.append("INPUT ANALYSIS:")
            total_strokes = sum(a.stroke_count for a in self.input_analysis)
            total_tree_nodes = sum(a.tree_node_count for a in self.input_analysis)
            invalid_anchors = sum(
                1 for a in self.input_analysis for anc in a.anchors if not anc.is_valid
            )
            lines.append(f"  Pages: {len(self.input_analysis)}")
            lines.append(f"  Total strokes: {total_strokes}")
            lines.append(f"  Total TreeNodeBlocks: {total_tree_nodes}")
            lines.append(f"  Invalid anchors: {invalid_anchors}")
            lines.append("")

        # Output summary
        if self.output_analysis:
            lines.append("OUTPUT ANALYSIS:")
            total_strokes = sum(a.stroke_count for a in self.output_analysis)
            total_tree_nodes = sum(a.tree_node_count for a in self.output_analysis)
            invalid_anchors = sum(
                1 for a in self.output_analysis for anc in a.anchors if not anc.is_valid
            )
            lines.append(f"  Pages: {len(self.output_analysis)}")
            lines.append(f"  Total strokes: {total_strokes}")
            lines.append(f"  Total TreeNodeBlocks: {total_tree_nodes}")
            lines.append(f"  Invalid anchors: {invalid_anchors}")
            if invalid_anchors > 0:
                lines.append("  *** INVALID ANCHORS DETECTED ***")
                for analysis in self.output_analysis:
                    for anchor in analysis.anchors:
                        if not anchor.is_valid:
                            lines.append(
                                f"    Page {analysis.page_index}: {anchor.node_id} "
                                f"anchor={anchor.anchor_offset} > "
                                f"page_text_len={anchor.page_text_len}"
                            )
            lines.append("")

        # Delta analysis
        if self.delta_analysis:
            lines.append("DELTA ANALYSIS:")
            for delta in self.delta_analysis:
                sign = "+" if delta.delta >= 0 else ""
                lines.append(
                    f"  Page {delta.page_index}: "
                    f"{delta.old_text_len} -> {delta.new_text_len} "
                    f"(delta={sign}{delta.delta})"
                )
                if delta.problematic_anchors:
                    for prob in delta.problematic_anchors:
                        lines.append(
                            f"    *** {prob['node_id']}: "
                            f"anchor {prob['original_anchor']} + delta = "
                            f"{prob['adjusted_anchor']} > {prob['new_page_text_len']}"
                        )
            lines.append("")

        report = "\n".join(lines)

        # Save report
        report_path = self.output_dir / "summary_report.txt"
        report_path.write_text(report)

        return report


def capture_sync_debug(
    session_name: str,
    input_rm_files: list[Path],
    input_markdown: Path,
    output_rm_data: dict[str, bytes],
    tracer: Any | None = None,
) -> DebugCapture:
    """Convenience function to capture a complete sync debug session.

    Args:
        session_name: Name for debug session
        input_rm_files: List of input .rm file paths
        input_markdown: Path to input markdown
        output_rm_data: Dict of output page_uuid -> .rm bytes
        tracer: Optional AnnotationTracer with events

    Returns:
        DebugCapture instance with all data saved
    """
    with DebugCapture(session_name) as capture:
        capture.save_input_rm_files(input_rm_files)
        capture.save_input_markdown(input_markdown)
        capture.save_output_rm_bytes(output_rm_data)
        if tracer:
            capture.save_trace(tracer)
        print(capture.generate_report())
        return capture
