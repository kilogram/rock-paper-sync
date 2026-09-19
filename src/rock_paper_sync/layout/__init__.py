"""Stub: the native layout engine has moved to ``native_layout/layout/``.

This package provides placeholder objects so that existing imports succeed.
Any actual use of a layout object raises ``NativeLayoutUnavailableError``.
"""

from rock_paper_sync.layout._sentinel import _make_sentinel
from rock_paper_sync.layout.device import DEFAULT_DEVICE, PAPER_PRO_MOVE, DeviceGeometry

ContentPaginator = _make_sentinel("ContentPaginator")
HEADER_ORPHAN_THRESHOLD_LINES = _make_sentinel("HEADER_ORPHAN_THRESHOLD_LINES")
LayoutContext = _make_sentinel("LayoutContext")
TextAreaConfig = _make_sentinel("TextAreaConfig")
WordWrapLayoutEngine = _make_sentinel("WordWrapLayoutEngine")

__all__ = [
    "ContentPaginator",
    "DEFAULT_DEVICE",
    "DeviceGeometry",
    "HEADER_ORPHAN_THRESHOLD_LINES",
    "LayoutContext",
    "PAPER_PRO_MOVE",
    "TextAreaConfig",
    "WordWrapLayoutEngine",
]
