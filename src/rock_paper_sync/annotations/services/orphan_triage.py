"""OrphanTriage — decides which stored orphans recover vs stay on the hidden layer.

Triage is pure computation: given an OrphanLedger (all DB orphan records for a document
plus any push-detected orphan IDs) and the new DocumentModel, it returns an OrphanDecision
that partitions records into recovered (visible layer) and preserved (hidden layer).

Usage::

    ledger = OrphanLedger.build(
        records=state.get_orphaned_annotations(vault, path),
        push_orphan_ids=push_orphan_ids,
    )
    decision = OrphanTriage().triage(ledger, new_model)

    # recovered → inject into new_model.annotations before projection
    # preserved → emit on PRESERVATION layer via HiddenLayerManager
    # excluded_ids → pass to _apply_annotations_to_page to block content-layer duplicates
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rock_paper_sync.annotations.services.hidden_layer import deserialize_annotation_blocks

if TYPE_CHECKING:
    from rock_paper_sync.annotations.document_model import (
        DocumentAnnotation,
        DocumentModel,
    )
    from rock_paper_sync.state import OrphanedAnnotation

logger = logging.getLogger(__name__)


# =============================================================================
# Value objects
# =============================================================================


@dataclass(frozen=True)
class OrphanRecord:
    """Orphaned annotation record that has a serialised blob (schema v8+).

    Constructed from OrphanedAnnotation rows via from_db(); rows without
    a blocks_blob (pre-v8 rows) yield None and are silently skipped.
    """

    annotation_id: str
    annotation_type: str  # "highlight" | "stroke"
    original_anchor_text: str | None
    source_page_idx: int | None
    blocks_blob: bytes

    @classmethod
    def from_db(cls, row: OrphanedAnnotation) -> OrphanRecord | None:
        if not row.blocks_blob:
            return None
        return cls(
            annotation_id=row.annotation_id,
            annotation_type=row.annotation_type,
            original_anchor_text=row.original_anchor_text,
            source_page_idx=row.source_page_idx,
            blocks_blob=row.blocks_blob,
        )


@dataclass(frozen=True)
class OrphanLedger:
    """All orphan inputs for a single generate_document() call.

    Replaces the three flat params that previously leaked onto generate_document():
    - orphan_blobs (hidden layer bytes)
    - orphan_annotation_ids (content-layer exclusions)
    - recovered_orphan_blobs (visible-layer promotions, not yet wired)

    Build via OrphanLedger.build() from raw DB rows + push_orphan_ids.
    """

    records: tuple[OrphanRecord, ...]
    push_orphan_ids: frozenset[str] = frozenset()

    @classmethod
    def build(
        cls,
        records: list[OrphanedAnnotation],
        push_orphan_ids: set[str] | frozenset[str] = frozenset(),
    ) -> OrphanLedger:
        valid = tuple(r for r in (OrphanRecord.from_db(o) for o in records) if r is not None)
        return cls(records=valid, push_orphan_ids=frozenset(push_orphan_ids))


@dataclass(frozen=True)
class RecoveredOrphan:
    """An orphan whose anchor text reappeared — ready for the visible layer."""

    record: OrphanRecord
    annotation: DocumentAnnotation  # resolved into new_model, contains original_rm_block


@dataclass(frozen=True)
class OrphanDecision:
    """Result of triage: partition of records into recovered vs preserved."""

    recovered: tuple[RecoveredOrphan, ...]
    preserved: tuple[OrphanRecord, ...]
    # excluded_ids = preserved annotation IDs ∪ push_orphan_ids.
    # Pass to _apply_annotations_to_page to prevent DUPLICATE_TREE_NODE.
    excluded_ids: frozenset[str]


# =============================================================================
# OrphanTriage service
# =============================================================================


class OrphanTriage:
    """Partitions orphan records into recovered vs preserved.

    Pure computation — no I/O, no DB writes. Testable in isolation.

    Re-anchoring uses AnchorContext.resolve() (exact → fuzzy → diff-anchor)
    rather than a substring check, so it handles typos, whitespace drift,
    and multiple occurrences correctly.

    Only highlights are recovered in this implementation. Strokes have
    a more complex block structure (SceneTreeBlock + TreeNodeBlock +
    SceneGroupItemBlock + SceneLineItemBlock) and are kept on the
    preservation layer until stroke recovery is implemented.
    """

    def __init__(self, fuzzy_threshold: float = 0.8) -> None:
        self._threshold = fuzzy_threshold

    def triage(self, ledger: OrphanLedger, new_model: DocumentModel) -> OrphanDecision:
        """Partition ledger records into recovered and preserved.

        Args:
            ledger: All orphan records for the document plus push_orphan_ids.
            new_model: The new DocumentModel (content only, no annotations yet).

        Returns:
            OrphanDecision with recovered/preserved split and excluded_ids.
        """
        recovered: list[RecoveredOrphan] = []
        preserved: list[OrphanRecord] = []

        for record in ledger.records:
            anno = self._try_recover(record, new_model)
            if anno is not None:
                recovered.append(RecoveredOrphan(record=record, annotation=anno))
                logger.info(
                    f"Recovered orphan {record.annotation_id[:8]} "
                    f"('{(record.original_anchor_text or '')[:40]}')"
                )
            else:
                preserved.append(record)

        excluded = ledger.push_orphan_ids | frozenset(r.annotation_id for r in preserved)
        return OrphanDecision(
            recovered=tuple(recovered),
            preserved=tuple(preserved),
            excluded_ids=excluded,
        )

    def _try_recover(
        self, record: OrphanRecord, new_model: DocumentModel
    ) -> DocumentAnnotation | None:
        """Attempt to re-anchor a single orphan record in new_model.

        Returns a DocumentAnnotation (with resolved anchor_context and
        original_rm_block) ready to be injected into the visible layer,
        or None if the anchor cannot be resolved at the required confidence.
        """
        from rock_paper_sync.annotations.document_model import AnchorContext, DocumentAnnotation

        if record.annotation_type != "highlight":
            # Stroke recovery requires reconstructing SceneTreeBlock/TreeNodeBlock/
            # SceneGroupItemBlock — deferred to a future implementation.
            return None

        if not record.original_anchor_text:
            return None

        # Build a minimal AnchorContext whose text_content is the stored anchor text.
        # Using the anchor text itself as the "document" gives correct hash + context
        # for Strategy 1 (exact hash match) in resolve().
        anchor = AnchorContext.from_text_span(
            full_text=record.original_anchor_text,
            start=0,
            end=len(record.original_anchor_text),
        )
        resolution = anchor.resolve(
            old_text=record.original_anchor_text,
            new_text=new_model.full_text,
            fuzzy_threshold=self._threshold,
        )

        if resolution is None or resolution.confidence < self._threshold:
            conf_str = f"{resolution.confidence:.2f}" if resolution is not None else "N/A"
            logger.debug(
                f"Orphan {record.annotation_id[:8]}: "
                f"could not resolve '{(record.original_anchor_text or '')[:30]}' "
                f"(confidence={conf_str})"
            )
            return None

        new_anchor = AnchorContext.from_text_span(
            full_text=new_model.full_text,
            start=resolution.start_offset,
            end=resolution.end_offset,
        )

        try:
            blocks = deserialize_annotation_blocks(record.blocks_blob)
        except Exception as exc:
            logger.warning(f"Orphan {record.annotation_id[:8]}: failed to deserialise blob: {exc}")
            return None

        if not blocks:
            return None

        # For highlights the blob contains a single SceneGlyphItemBlock.
        primary_block = blocks[0]

        return DocumentAnnotation(
            annotation_id=record.annotation_id,
            annotation_type="highlight",
            anchor_context=new_anchor,
            original_rm_block=primary_block,
            source_page_idx=record.source_page_idx,
        )
