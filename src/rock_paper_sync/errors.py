"""Typed errors for rock-paper-sync.

Errors defined here are part of the public API: callers may branch on them.
"""


class NativeLayoutUnavailableError(Exception):
    """The native typed-text layout engine has moved to ``native_layout/``.

    Raised when production code attempts to use a layout feature that is
    no longer part of the shipped package.  Pull sync and annotation
    processing depend on native layout and are unavailable until the
    PDF-substrate pull path lands (Phase 4).

    CLI handling: the ``sync`` command catches this and prints a single
    diagnostic line instead of a traceback.
    """
