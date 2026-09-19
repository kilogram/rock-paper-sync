"""Typed errors for rock-paper-sync.

Errors defined here are part of the public API: callers may branch on them.
"""

__all__ = [
    "NativeLayoutUnavailableError",
    "RenderMismatchError",
    "TransformMismatchError",
]


class NativeLayoutUnavailableError(Exception):
    """The native typed-text layout engine has moved to ``native_layout/``.

    Raised when production code attempts to use a layout feature that is
    no longer part of the shipped package.  Pull sync and annotation
    processing depend on native layout and are unavailable until the
    PDF-substrate pull path lands (Phase 4).

    CLI handling: the ``sync`` command catches this and prints a single
    diagnostic line instead of a traceback.
    """


class RenderMismatchError(Exception):
    """PDF on device does not match the last pushed PDF (I10).

    Raised when pull detects that the ``.pdf`` entry hash in the cloud
    document index differs from ``sync_state.pushed_pdf_sha256``.
    Ink cannot be interpreted against a different PDF.

    Attributes:
        doc_uuid: Document UUID.
        expected_hash: The stored ``pushed_pdf_sha256``.
        actual_hash: The hash found in the cloud index.
    """

    def __init__(self, doc_uuid: str, expected_hash: str, actual_hash: str | None) -> None:
        self.doc_uuid = doc_uuid
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        super().__init__(
            f"PDF mismatch for '{doc_uuid}': "
            f"expected {expected_hash[:12]}…, "
            f"found {(actual_hash or 'none')[:12]}…. "
            f"Re-push the document before pulling ink."
        )


class TransformMismatchError(Exception):
    """I8 cross-check failed: words under highlight rects != GlyphRange text.

    Attributes:
        page: PDF page index.
        expected_words: Words from ``GlyphRange.text``.
        found_words: Words found under the transformed rects.
    """

    def __init__(
        self, page: int, expected_words: tuple[str, ...], found_words: tuple[str, ...]
    ) -> None:
        self.page = page
        self.expected_words = expected_words
        self.found_words = found_words
        super().__init__(
            f"Transform mismatch on page {page}: "
            f"expected {expected_words}, found {found_words}. "
            f"The device-to-PDF transform may be wrong."
        )
