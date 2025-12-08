"""Annotation tracing infrastructure using contextvars.

This module provides zero-plumbing tracing for annotation flow through
the generator. Use trace_event() from anywhere - no need to pass tracer
objects through call stacks.

Usage:
    from tools.rmlib.tracer import AnnotationTracer, trace_event

    # Enable tracing with context manager
    with AnnotationTracer() as tracer:
        # Anywhere in generator code, just call:
        trace_event("route", node_id="2:763", target_page=1, anchor=173)

        # Get the report
        print(tracer.report())

    # Or run as a script to trace a sync operation:
    # uv run python -m tools.rmlib.tracer --rm-files input/*.rm --output trace.json
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

# Global context variable - no plumbing needed
_current_tracer: ContextVar[AnnotationTracer | None] = ContextVar(
    "annotation_tracer", default=None
)


@dataclass
class TraceEvent:
    """A single traced event in the annotation lifecycle."""

    phase: str  # "extract", "route", "relocate", "generate", "validate"
    timestamp: float
    annotation_id: str  # e.g., "2:763" for node_id
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "timestamp": self.timestamp,
            "annotation_id": self.annotation_id,
            "data": self.data,
        }


@dataclass
class AnnotationTracer:
    """Tracer that captures annotation lifecycle events via contextvars.

    Usage:
        with AnnotationTracer() as tracer:
            # ... run sync ...
            print(tracer.report("text"))
    """

    events: list[TraceEvent] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    _token: Any = field(default=None, repr=False)

    def __enter__(self) -> AnnotationTracer:
        """Activate this tracer in the current context."""
        self._token = _current_tracer.set(self)
        self.start_time = time.time()
        return self

    def __exit__(self, *args: object) -> None:
        """Deactivate this tracer."""
        if self._token is not None:
            _current_tracer.reset(self._token)

    def add_event(
        self, phase: str, annotation_id: str, data: dict[str, Any]
    ) -> None:
        """Add a trace event."""
        self.events.append(
            TraceEvent(
                phase=phase,
                timestamp=time.time() - self.start_time,
                annotation_id=annotation_id,
                data=data,
            )
        )

    def get_events_for(self, annotation_id: str) -> list[TraceEvent]:
        """Get all events for a specific annotation."""
        return [e for e in self.events if e.annotation_id == annotation_id]

    def report(self, format: Literal["text", "json"] = "text") -> str:
        """Generate a human-readable or JSON report of all traced events.

        Args:
            format: "text" for human-readable, "json" for machine-readable
        """
        if format == "json":
            return self._report_json()
        return self._report_text()

    def _report_text(self) -> str:
        """Generate human-readable text report."""
        lines = []
        lines.append("=" * 60)
        lines.append("ANNOTATION TRACE REPORT")
        lines.append("=" * 60)
        lines.append(f"Total events: {len(self.events)}")
        lines.append("")

        # Group by annotation ID
        by_annotation: dict[str, list[TraceEvent]] = defaultdict(list)
        for event in self.events:
            by_annotation[event.annotation_id].append(event)

        for ann_id, events in sorted(by_annotation.items()):
            lines.append(f"Annotation {ann_id}:")
            for event in events:
                ts = f"{event.timestamp:.3f}s"
                data_str = ", ".join(f"{k}={v}" for k, v in event.data.items())
                lines.append(f"  [{ts}] {event.phase}: {data_str}")
            lines.append("")

        # Summary: show cross-page movements
        cross_page = []
        for ann_id, events in by_annotation.items():
            source_pages = set()
            target_pages = set()
            for e in events:
                if "source_page" in e.data:
                    source_pages.add(e.data["source_page"])
                if "target_page" in e.data:
                    target_pages.add(e.data["target_page"])
            if source_pages and target_pages and source_pages != target_pages:
                cross_page.append((ann_id, source_pages, target_pages))

        if cross_page:
            lines.append("CROSS-PAGE MOVEMENTS:")
            for ann_id, src, tgt in cross_page:
                lines.append(f"  {ann_id}: page {src} -> page {tgt}")
            lines.append("")

        # Show any anchor calculations
        anchor_events = [e for e in self.events if "anchor" in e.data]
        if anchor_events:
            lines.append("ANCHOR CALCULATIONS:")
            for event in anchor_events:
                lines.append(
                    f"  {event.annotation_id} @ {event.phase}: "
                    f"anchor={event.data.get('anchor')}"
                )
            lines.append("")

        return "\n".join(lines)

    def _report_json(self) -> str:
        """Generate JSON report."""
        return json.dumps(
            {
                "total_events": len(self.events),
                "events": [e.to_dict() for e in self.events],
            },
            indent=2,
        )


def get_current_tracer() -> AnnotationTracer | None:
    """Get the current active tracer, if any."""
    return _current_tracer.get()


def trace_event(phase: str, annotation_id: str, **data: Any) -> None:
    """Trace an annotation event. Call from anywhere - no plumbing needed.

    If no tracer is active, this is a no-op.

    Args:
        phase: The phase of annotation processing (extract, route, relocate, etc.)
        annotation_id: Unique ID for the annotation (e.g., "2:763" for CrdtId)
        **data: Additional key-value data to record

    Example:
        trace_event("route", "2:763",
                   source_page=0,
                   target_page=1,
                   anchor=173,
                   reason="y_position moved below page boundary")
    """
    tracer = _current_tracer.get()
    if tracer is not None:
        tracer.add_event(phase, annotation_id, data)


def trace_anchor_calc(
    annotation_id: str,
    *,
    source_page: int,
    target_page: int,
    old_anchor: int | None,
    new_anchor: int,
    page_text_len: int,
    reason: str = "",
) -> None:
    """Convenience function to trace anchor calculations with common fields.

    Args:
        annotation_id: The node ID (e.g., "2:763")
        source_page: Original page index
        target_page: New page index
        old_anchor: Previous anchor value (None if new annotation)
        new_anchor: Calculated anchor value
        page_text_len: Length of target page's RootTextBlock text
        reason: Why this anchor was calculated
    """
    trace_event(
        "anchor_calc",
        annotation_id,
        source_page=source_page,
        target_page=target_page,
        old_anchor=old_anchor,
        new_anchor=new_anchor,
        page_text_len=page_text_len,
        is_valid=0 <= new_anchor <= page_text_len,
        reason=reason,
    )


# CLI interface for running trace on existing files
if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Trace annotation processing on .rm files"
    )
    parser.add_argument(
        "--rm-files",
        type=Path,
        nargs="+",
        help="Input .rm files to analyze",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output file for trace report",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )

    args = parser.parse_args()

    if not args.rm_files:
        print("No .rm files specified", file=sys.stderr)
        sys.exit(1)

    # For now, just show what we would trace
    print("Tracer initialized. To trace actual sync operations:")
    print("  from tools.rmlib.tracer import AnnotationTracer, trace_event")
    print("  with AnnotationTracer() as tracer:")
    print("      # ... run sync ...")
    print("      print(tracer.report())")
