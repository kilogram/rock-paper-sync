"""Diagnostic library for reMarkable file format analysis.

This library provides tools for:
- Inspecting .rm file structure
- Tracing annotation flow through the generator
- Validating anchor calculations
- Comparing .rm files
- Capturing debug data for investigation

Usage:
    from tools.rmlib import trace_event, AnnotationTracer, DebugCapture
    from tools.rmlib.validator import validate_rm_file

    # Enable tracing with context manager
    with AnnotationTracer() as tracer:
        # ... run sync operation ...
        print(tracer.report())

    # Validate output
    errors = validate_rm_file(output_rm_path, page_text)

    # Capture debug data (NOT committed to git)
    with DebugCapture("my_debug_session") as capture:
        capture.save_input_rm_files(rm_files)
        # ... run operations ...
        capture.generate_report()
"""

from tools.rmlib.capture import DebugCapture, capture_sync_debug
from tools.rmlib.tracer import (
    AnnotationTracer,
    TraceEvent,
    get_current_tracer,
    trace_event,
)

__all__ = [
    "AnnotationTracer",
    "DebugCapture",
    "TraceEvent",
    "capture_sync_debug",
    "get_current_tracer",
    "trace_event",
]
