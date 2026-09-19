"""Apply ink edits to vault markdown files.

Computes ``MarkdownEdit``s from dispositions, then applies them to the
current vault file using diff-match-patch ``patch_make``/``patch_apply``
against the pushed snapshot text.

Any patch that fails to apply turns its cluster's disposition into
``Deferred("patch_failed")``.

Also provides ``strip_uninterpreted_markers`` for the push side (called
from ``render/html.py``'s existing marker stripping).

Invariants relied upon:
    INV-I12: Pull is crash-safe toward duplicates, not loss.
             Write order: attachments → markdown → SQLite.

Import restrictions (enforced by import-linter):
    Must NOT import ``weasyprint``, ``fitz``, ``pymupdf``,
    ``rock_paper_sync.sync_v3``, or ``rock_paper_sync.rm_cloud_sync``.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from diff_match_patch import diff_match_patch

from rock_paper_sync.ink.dispositions import (
    InkAnchor,
    MarkdownEdit,
    paragraph_anchor_ref,
)

__all__ = [
    "apply_edits",
    "build_token_spans",
    "compute_paragraph_anchors",
    "strip_uninterpreted_markers",
]

logger = logging.getLogger(__name__)

# Pattern for UNINTERPRETED marker blocks
_UNINTERPRETED_RE = re.compile(
    r"<!-- RPS:UNINTERPRETED\b[^>]*-->.*?<!-- RPS:END -->",
    re.DOTALL,
)


def strip_uninterpreted_markers(text: str) -> str:
    """Remove UNINTERPRETED marker blocks from text.

    Called from the push side before rendering to PDF.

    Args:
        text: Markdown text potentially containing marker blocks.

    Returns:
        Text with all ``<!-- RPS:UNINTERPRETED ... --> ... <!-- RPS:END -->``
        blocks removed.
    """
    return _UNINTERPRETED_RE.sub("", text).strip()


def compute_paragraph_anchors(snapshot_text: str) -> dict[str, InkAnchor]:
    """Compute paragraph anchors from pushed snapshot text.

    Splits on double newlines to find paragraph boundaries, normalises
    each paragraph's text, and computes a sha256-based ref with an ordinal
    for duplicates.

    Args:
        snapshot_text: The markdown text at push time.

    Returns:
        Mapping from ``paragraph_anchor_ref`` string to ``InkAnchor``.
    """
    paragraphs = _split_paragraphs(snapshot_text)
    text_counts: dict[str, int] = {}
    anchors: dict[str, InkAnchor] = {}

    for para_text in paragraphs:
        normalised = _normalise_paragraph(para_text)
        if not normalised:
            continue
        ordinal = text_counts.get(normalised, 0)
        text_counts[normalised] = ordinal + 1
        ref = paragraph_anchor_ref(normalised, ordinal)
        anchors[ref] = InkAnchor(kind="paragraph", ref=ref)

    return anchors


def _split_paragraphs(text: str) -> list[str]:
    """Split markdown text into paragraphs (blocks separated by blank lines)."""
    # Split on double newlines, strip each
    blocks = re.split(r"\n\s*\n", text)
    return [b.strip() for b in blocks if b.strip()]


def _normalise_paragraph(text: str) -> str:
    """Normalise paragraph text for anchor computation.

    Collapses whitespace and strips leading/trailing whitespace.
    """
    return " ".join(text.split())


def find_paragraph_in_text(
    text: str,
    anchor: InkAnchor,
    snapshot_text: str,
) -> tuple[int, int] | None:
    """Find a paragraph's position in the current text using its anchor.

    Looks up the normalised paragraph text from the snapshot using the
    anchor ref, then searches for it in the current text.

    Args:
        text: Current markdown text.
        anchor: The paragraph anchor to find.
        snapshot_text: The snapshot text the anchor was computed from.

    Returns:
        ``(start, end)`` character offsets in ``text``, or None if not found.
    """
    if anchor.kind == "document_end":
        return (len(text), len(text))

    # Find the paragraph text from the snapshot
    paragraphs = _split_paragraphs(snapshot_text)
    text_counts: dict[str, int] = {}

    target_text: str | None = None
    for para in paragraphs:
        normalised = _normalise_paragraph(para)
        if not normalised:
            continue
        ordinal = text_counts.get(normalised, 0)
        text_counts[normalised] = ordinal + 1
        ref = paragraph_anchor_ref(normalised, ordinal)
        if ref == anchor.ref:
            target_text = para
            break

    if target_text is None:
        return None

    # Search in the current text
    idx = text.find(target_text)
    if idx >= 0:
        return (idx, idx + len(target_text))

    # Try normalised search
    norm_target = _normalise_paragraph(target_text)
    current_paras = _split_paragraphs(text)
    offset = 0
    for para in current_paras:
        para_idx = text.find(para, offset)
        if para_idx < 0:
            continue
        if _normalise_paragraph(para) == norm_target:
            return (para_idx, para_idx + len(para))
        offset = para_idx + len(para)

    return None


def build_token_spans(
    texts: tuple[str, ...],
    snapshot: str,
) -> list[tuple[int, int]]:
    """Map each token to its ``(start, end)`` character span in the snapshot.

    Scans the snapshot greedily in reading order — the same order the
    renderer's ``_tokenise_text`` produces.  Tokens that are not literal
    substrings of the markdown (e.g. synthetic list markers like ``•``)
    get ``(-1, -1)``.

    Used by ``pull_sync`` to compute ``MarkdownEdit.snapshot_span`` so
    that highlights are applied at the correct position, not at the
    first substring match.

    Args:
        texts: Token texts in reading order (from ``WordMap.texts``).
        snapshot: The pushed snapshot markdown text.

    Returns:
        List of ``(start, end)`` per token, same length as *texts*.
    """
    spans: list[tuple[int, int]] = []
    search_from = 0
    for text in texts:
        idx = snapshot.find(text, search_from)
        if idx >= 0:
            end = idx + len(text)
            spans.append((idx, end))
            search_from = end
        else:
            spans.append((-1, -1))
    return spans


def apply_edits(
    edits: list[MarkdownEdit],
    snapshot_text: str,
    current_text: str,
    notes_heading: str = "Notes",
) -> tuple[str, list[str]]:
    """Apply edits to the current vault file text.

    Uses diff-match-patch to patch the current text based on edits
    computed against the snapshot text.

    Args:
        edits: Edits to apply.
        snapshot_text: The pushed snapshot text edits were computed against.
        current_text: The current vault file text.
        notes_heading: Heading for appended notes section.

    Returns:
        Tuple of (new_text, failed_cluster_ids).
        Failed cluster ids should have their disposition changed to Deferred.
    """
    dmp = diff_match_patch()
    result_text = current_text
    failed: list[str] = []

    # Group edits by kind for ordered application
    highlights = [e for e in edits if e.kind == "highlight"]
    appends = [e for e in edits if e.kind == "append"]
    embeds = [e for e in edits if e.kind == "embed"]
    deferred_edits = [e for e in edits if e.kind == "deferred"]
    gesture_edits = [
        e for e in edits if e.kind in ("tick", "strikethrough", "insertion", "correction")
    ]

    # Apply highlights (wrap in ==...==).
    #
    # When ``snapshot_span`` is set (token-based location from the word map),
    # the edit targets the exact character range in the snapshot, mapped to
    # the current text via diff-match-patch.  This avoids hitting an earlier
    # prose occurrence of the same phrase.
    #
    # When ``snapshot_span`` is None (fallback — no tokens found for the
    # highlight rects), the edit wraps the first bare substring occurrence.
    _highlight_applied: dict[str, int] = {}
    for edit in highlights:
        phrase = edit.text
        already = _highlight_applied.get(phrase, 0)

        # Idempotent: count how many are wrapped; skip if enough
        wrapped_marker = f"=={phrase}=="
        n_wrapped = _count_occurrences(result_text, wrapped_marker)
        if n_wrapped > already:
            logger.debug(f"Highlight already applied for cluster {edit.cluster_id}, skipping")
            _highlight_applied[phrase] = already + 1
            continue

        if edit.snapshot_span is not None and edit.snapshot_span[0] >= 0:
            # Token-based: wrap at the exact span in the snapshot, mapped
            # to current text via diff-match-patch.
            ss, se = edit.snapshot_span
            patched_snapshot = snapshot_text[:ss] + f"=={phrase}==" + snapshot_text[se:]
            patches = dmp.patch_make(snapshot_text, patched_snapshot)
            patched, results = dmp.patch_apply(patches, result_text)
            if all(results):
                result_text = patched
                _highlight_applied[phrase] = already + 1
            else:
                logger.warning(
                    f"Token-span patch failed for cluster {edit.cluster_id}, "
                    f"falling back to substring"
                )
                applied = _wrap_nth_bare_occurrence(result_text, phrase, 0)
                if applied is not None:
                    result_text = applied
                    _highlight_applied[phrase] = already + 1
                else:
                    failed.append(edit.cluster_id)
        else:
            # Substring fallback: wrap the first bare occurrence.
            applied = _wrap_nth_bare_occurrence(result_text, phrase, 0)
            if applied is not None:
                result_text = applied
                _highlight_applied[phrase] = already + 1
            else:
                # Try patch as last resort
                patches = dmp.patch_make(
                    snapshot_text, snapshot_text.replace(phrase, f"=={phrase}==", 1)
                )
                patched, results = dmp.patch_apply(patches, result_text)
                if all(results):
                    result_text = patched
                    _highlight_applied[phrase] = already + 1
                else:
                    logger.warning(f"Failed to apply highlight for cluster {edit.cluster_id}")
                    failed.append(edit.cluster_id)

    # Apply gesture edits (tick, strikethrough, insertion, correction).
    # Each gesture has a snapshot_span and replacement_text computed by
    # the gesture pipeline.  We apply them via diff-match-patch just
    # like highlights, mapping the snapshot edit to the current text.
    for edit in gesture_edits:
        if edit.snapshot_span is None or edit.snapshot_span[0] < 0:
            logger.warning(f"Gesture edit {edit.cluster_id} has no snapshot span, skipping")
            failed.append(edit.cluster_id)
            continue
        ss, se = edit.snapshot_span
        replacement = edit.text if edit.text else ""
        patched_snapshot = snapshot_text[:ss] + replacement + snapshot_text[se:]
        patches = dmp.patch_make(snapshot_text, patched_snapshot)
        patched, results = dmp.patch_apply(patches, result_text)
        if all(results):
            result_text = patched
        else:
            logger.warning(f"Gesture patch failed for cluster {edit.cluster_id}")
            failed.append(edit.cluster_id)

    # Apply embeds (insert ![[name.svg]] after anchor paragraph)
    for edit in embeds:
        pos = find_paragraph_in_text(result_text, edit.anchor, snapshot_text)
        if pos is not None:
            end = pos[1]
            # Insert after the paragraph (with a newline)
            insert = f"\n{edit.text}\n"
            result_text = result_text[:end] + insert + result_text[end:]
        else:
            logger.warning(f"Failed to find anchor for embed cluster {edit.cluster_id}")
            failed.append(edit.cluster_id)

    # Apply appends (under ## Notes heading)
    if appends:
        notes_section = f"\n\n## {notes_heading}\n\n"
        notes_texts = [e.text for e in appends]

        if f"## {notes_heading}" in result_text:
            # Find existing notes section and append to it
            idx = result_text.index(f"## {notes_heading}")
            # Find end of that section (next ## or end of document)
            section_end = len(result_text)
            next_heading = result_text.find("\n## ", idx + len(f"## {notes_heading}"))
            if next_heading > 0:
                section_end = next_heading

            insert_text = "\n".join(notes_texts) + "\n"
            result_text = result_text[:section_end] + "\n" + insert_text + result_text[section_end:]
        else:
            # Add notes section at end
            result_text = result_text.rstrip() + notes_section + "\n".join(notes_texts) + "\n"

    # Apply deferred markers
    for edit in deferred_edits:
        pos = find_paragraph_in_text(result_text, edit.anchor, snapshot_text)
        if pos is not None:
            end = pos[1]
            result_text = result_text[:end] + f"\n{edit.text}\n" + result_text[end:]
        else:
            # Append at end if anchor not found
            result_text = result_text.rstrip() + f"\n\n{edit.text}\n"

    return result_text, failed


def _count_occurrences(text: str, needle: str) -> int:
    """Count non-overlapping occurrences of *needle* in *text*."""
    count = 0
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx < 0:
            break
        count += 1
        start = idx + len(needle)
    return count


def _wrap_nth_bare_occurrence(text: str, phrase: str, n: int) -> str | None:
    """Wrap the *n*-th bare (not already ``==``-wrapped) occurrence of *phrase*.

    Returns the modified text, or ``None`` if fewer than *n+1* bare
    occurrences exist.
    """
    start = 0
    bare_count = 0
    while True:
        idx = text.find(phrase, start)
        if idx < 0:
            return None
        # Check if this occurrence is already wrapped
        prefix_ok = idx >= 2 and text[idx - 2 : idx] == "=="
        suffix_ok = (idx + len(phrase) + 2 <= len(text)) and text[
            idx + len(phrase) : idx + len(phrase) + 2
        ] == "=="
        if prefix_ok and suffix_ok:
            # Already wrapped — skip
            start = idx + len(phrase) + 2
            continue
        if bare_count == n:
            return text[:idx] + f"=={phrase}==" + text[idx + len(phrase) :]
        bare_count += 1
        start = idx + len(phrase)
    return None  # unreachable but keeps mypy happy


def write_markdown_atomic(path: Path, content: str) -> None:
    """Atomically replace a markdown file (I12).

    Writes to a temp file in the same directory, then ``os.replace``.

    Args:
        path: Path to the markdown file.
        content: New file content.
    """
    dir_path = path.parent
    fd, tmp_path_str = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up temp file on any failure
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
