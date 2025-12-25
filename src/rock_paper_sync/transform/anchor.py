"""Anchor resolution utilities for annotation relocation.

This module provides pure functions for resolving where an annotation's
anchor point has moved to when text changes. Both highlights and strokes
need to find their new anchor position in modified text.

Design principles:
- Pure functions (no side effects, no rmscene imports)
- Multiple resolution strategies with fallbacks
- Confidence scoring for match quality
- Handlers use these to find their new anchor offset

Resolution strategies (in order of preference):
1. Exact match - text content unchanged, same position
2. Fuzzy match - text content found nearby
3. Context match - surrounding text matches
4. Fallback - best effort based on relative position

Usage:
    from rock_paper_sync.transform import resolve_anchor, AnchorResolution

    result = resolve_anchor(
        anchor_text="important concept",
        old_offset=150,
        old_text=old_doc,
        new_text=new_doc,
        context_before="discussing the ",
        context_after=" in detail",
    )
    if result.confidence > 0.8:
        new_offset = result.new_offset
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from .types import AnchorResolution

logger = logging.getLogger(__name__)


def resolve_anchor(
    anchor_text: str,
    old_offset: int,
    old_text: str,
    new_text: str,
    context_before: str = "",
    context_after: str = "",
    search_radius: int = 500,
) -> AnchorResolution | None:
    """Resolve anchor position in new text.

    Tries multiple strategies to find where the anchor text has moved:
    1. Exact match at same position
    2. Exact match nearby (within search_radius)
    3. Context-based match using surrounding text
    4. Fuzzy match for minor text changes

    Args:
        anchor_text: The text content being anchored to
        old_offset: Original character offset in old_text
        old_text: Document text before modification
        new_text: Document text after modification
        context_before: Text that appeared before anchor (for context matching)
        context_after: Text that appeared after anchor (for context matching)
        search_radius: How far to search from expected position

    Returns:
        AnchorResolution with new offset and confidence, or None if not found
    """
    if not anchor_text:
        return None

    # Strategy 1: Exact match at same position
    result = _try_exact_match_at_position(anchor_text, old_offset, new_text)
    if result:
        return result

    # Strategy 2: Exact match nearby
    result = _try_exact_match_nearby(anchor_text, old_offset, new_text, search_radius)
    if result:
        return result

    # Strategy 3: Context-based match
    if context_before or context_after:
        result = _try_context_match(
            anchor_text, context_before, context_after, new_text, old_offset
        )
        if result:
            return result

    # Strategy 4: Fuzzy match for minor edits
    result = _try_fuzzy_match(anchor_text, old_offset, old_text, new_text)
    if result:
        return result

    logger.debug(f"Could not resolve anchor '{anchor_text[:30]}...' in new text")
    return None


def _try_exact_match_at_position(
    anchor_text: str,
    old_offset: int,
    new_text: str,
) -> AnchorResolution | None:
    """Try to find exact match at the same position."""
    if old_offset < 0 or old_offset + len(anchor_text) > len(new_text):
        return None

    if new_text[old_offset : old_offset + len(anchor_text)] == anchor_text:
        return AnchorResolution(
            old_offset=old_offset,
            new_offset=old_offset,
            confidence=1.0,
            match_type="exact",
        )
    return None


def _try_exact_match_nearby(
    anchor_text: str,
    old_offset: int,
    new_text: str,
    search_radius: int,
) -> AnchorResolution | None:
    """Search for exact match within radius of expected position."""
    # Calculate search bounds
    search_start = max(0, old_offset - search_radius)
    search_end = min(len(new_text), old_offset + search_radius + len(anchor_text))
    search_region = new_text[search_start:search_end]

    # Find all occurrences in search region
    occurrences = []
    pos = 0
    while True:
        idx = search_region.find(anchor_text, pos)
        if idx == -1:
            break
        absolute_offset = search_start + idx
        distance = abs(absolute_offset - old_offset)
        occurrences.append((absolute_offset, distance))
        pos = idx + 1

    if not occurrences:
        return None

    # Return closest occurrence
    best_offset, best_distance = min(occurrences, key=lambda x: x[1])

    # Confidence decreases with distance
    confidence = max(0.5, 1.0 - (best_distance / search_radius) * 0.5)

    return AnchorResolution(
        old_offset=old_offset,
        new_offset=best_offset,
        confidence=confidence,
        match_type="exact_nearby",
    )


def _try_context_match(
    anchor_text: str,
    context_before: str,
    context_after: str,
    new_text: str,
    old_offset: int,
) -> AnchorResolution | None:
    """Find anchor using surrounding context."""
    # Build pattern: context_before + anchor + context_after
    # Try progressively shorter context until match found

    for ctx_len in [50, 30, 15, 5]:
        before = context_before[-ctx_len:] if len(context_before) >= ctx_len else context_before
        after = context_after[:ctx_len] if len(context_after) >= ctx_len else context_after

        if before:
            pattern = before + anchor_text
            idx = new_text.find(pattern)
            if idx != -1:
                new_offset = idx + len(before)
                return AnchorResolution(
                    old_offset=old_offset,
                    new_offset=new_offset,
                    confidence=0.85 + (ctx_len / 100),  # More context = higher confidence
                    match_type="context",
                )

        if after:
            pattern = anchor_text + after
            idx = new_text.find(pattern)
            if idx != -1:
                return AnchorResolution(
                    old_offset=old_offset,
                    new_offset=idx,
                    confidence=0.85 + (ctx_len / 100),
                    match_type="context",
                )

    return None


def _try_fuzzy_match(
    anchor_text: str,
    old_offset: int,
    old_text: str,
    new_text: str,
    similarity_threshold: float = 0.8,
) -> AnchorResolution | None:
    """Find anchor with minor text changes using fuzzy matching."""
    # Get region around old offset in new text
    search_start = max(0, old_offset - 200)
    search_end = min(len(new_text), old_offset + len(anchor_text) + 200)

    best_match = None
    best_ratio = 0.0

    # Slide window looking for similar text
    window_size = len(anchor_text)
    for i in range(
        search_start, min(search_end - window_size + 1, len(new_text) - window_size + 1)
    ):
        candidate = new_text[i : i + window_size]
        ratio = SequenceMatcher(None, anchor_text, candidate).ratio()

        if ratio > best_ratio and ratio >= similarity_threshold:
            best_ratio = ratio
            best_match = i

    if best_match is not None:
        return AnchorResolution(
            old_offset=old_offset,
            new_offset=best_match,
            confidence=best_ratio * 0.9,  # Fuzzy matches get slightly lower confidence
            match_type="fuzzy",
        )

    return None


def resolve_by_relative_position(
    old_offset: int,
    old_text: str,
    new_text: str,
) -> AnchorResolution:
    """Fallback: estimate new offset based on relative position.

    When text matching fails, estimate based on where we were in the
    document. If we were 30% through old text, assume 30% through new.

    This is a last resort with low confidence.

    Args:
        old_offset: Original character offset
        old_text: Document text before modification
        new_text: Document text after modification

    Returns:
        AnchorResolution with estimated position (low confidence)
    """
    if len(old_text) == 0:
        return AnchorResolution(
            old_offset=old_offset,
            new_offset=0,
            confidence=0.1,
            match_type="fallback",
        )

    relative_pos = old_offset / len(old_text)
    new_offset = int(relative_pos * len(new_text))
    new_offset = max(0, min(new_offset, len(new_text)))

    return AnchorResolution(
        old_offset=old_offset,
        new_offset=new_offset,
        confidence=0.3,
        match_type="fallback",
    )


def find_all_occurrences(
    text: str,
    pattern: str,
) -> list[int]:
    """Find all occurrences of pattern in text.

    Args:
        text: Text to search in
        pattern: Pattern to find

    Returns:
        List of starting offsets where pattern occurs
    """
    occurrences = []
    pos = 0
    while True:
        idx = text.find(pattern, pos)
        if idx == -1:
            break
        occurrences.append(idx)
        pos = idx + 1
    return occurrences
