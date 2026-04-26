"""Unit tests for OrphanTriage service and OrphanLedger value objects."""

from __future__ import annotations

import io

import rmscene

from rock_paper_sync.annotations.services.orphan_triage import (
    OrphanLedger,
    OrphanRecord,
    OrphanTriage,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_record(
    annotation_id: str = "anno-1",
    annotation_type: str = "highlight",
    original_anchor_text: str | None = "preserved forever",
    source_page_idx: int | None = None,
    blocks_blob: bytes | None = None,
) -> OrphanRecord:
    return OrphanRecord(
        annotation_id=annotation_id,
        annotation_type=annotation_type,
        original_anchor_text=original_anchor_text,
        source_page_idx=source_page_idx,
        blocks_blob=blocks_blob or _minimal_blob(),
    )


def _minimal_blob() -> bytes:
    """Minimal valid rmscene blob — a subset of blocks from a generated .rm page."""
    from rock_paper_sync.annotations.domain import PageTransformPlan
    from rock_paper_sync.annotations.scene_adapter import PageTransformExecutor
    from rock_paper_sync.layout.device import DEFAULT_DEVICE

    executor = PageTransformExecutor(DEFAULT_DEVICE)
    plan = PageTransformPlan(page_uuid="test-uuid", page_text="hello")
    rm_bytes = executor.execute(plan)
    all_blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
    subset = all_blocks[:2]
    buf = io.BytesIO()
    rmscene.write_blocks(buf, subset)
    return buf.getvalue()


def _make_new_model(text: str):
    """Build a minimal DocumentModel from a plain text string."""
    from rock_paper_sync.annotations.document_model import DocumentModel
    from rock_paper_sync.layout.device import DEFAULT_DEVICE
    from rock_paper_sync.parser import BlockType, ContentBlock

    block = ContentBlock(type=BlockType.PARAGRAPH, level=0, text=text)
    return DocumentModel.from_content_blocks([block], DEFAULT_DEVICE)


# =============================================================================
# OrphanRecord
# =============================================================================


class TestOrphanRecord:
    def test_from_db_with_blob(self):
        from rock_paper_sync.state import OrphanedAnnotation

        row = OrphanedAnnotation(
            vault_name="vault",
            obsidian_path="file.md",
            annotation_id="a1",
            annotation_type="highlight",
            original_anchor_text="hello",
            orphaned_at=0,
            blocks_blob=b"\x01\x02",
            source_page_idx=2,
        )
        record = OrphanRecord.from_db(row)
        assert record is not None
        assert record.annotation_id == "a1"
        assert record.original_anchor_text == "hello"
        assert record.source_page_idx == 2
        assert record.blocks_blob == b"\x01\x02"

    def test_from_db_without_blob_returns_none(self):
        from rock_paper_sync.state import OrphanedAnnotation

        row = OrphanedAnnotation(
            vault_name="vault",
            obsidian_path="file.md",
            annotation_id="a1",
            annotation_type="highlight",
            original_anchor_text="hello",
            orphaned_at=0,
            blocks_blob=None,
        )
        assert OrphanRecord.from_db(row) is None


# =============================================================================
# OrphanLedger.build
# =============================================================================


class TestOrphanLedgerBuild:
    def test_filters_rows_without_blobs(self):
        from rock_paper_sync.state import OrphanedAnnotation

        rows = [
            OrphanedAnnotation("v", "f", "a1", "highlight", None, 0, b"\x01", None),
            OrphanedAnnotation("v", "f", "a2", "highlight", None, 0, None, None),
        ]
        ledger = OrphanLedger.build(rows)
        assert len(ledger.records) == 1
        assert ledger.records[0].annotation_id == "a1"

    def test_push_orphan_ids_stored_as_frozenset(self):
        ledger = OrphanLedger.build([], push_orphan_ids={"x", "y"})
        assert ledger.push_orphan_ids == frozenset({"x", "y"})

    def test_empty_input(self):
        ledger = OrphanLedger.build([])
        assert ledger.records == ()
        assert ledger.push_orphan_ids == frozenset()


# =============================================================================
# OrphanTriage.triage — empty ledger
# =============================================================================


class TestOrphanTriageEmpty:
    def test_empty_ledger_returns_empty_decision(self):
        model = _make_new_model("some text")
        ledger = OrphanLedger(records=())
        decision = OrphanTriage().triage(ledger, model)

        assert decision.recovered == ()
        assert decision.preserved == ()
        assert decision.excluded_ids == frozenset()

    def test_push_orphan_ids_included_in_excluded(self):
        model = _make_new_model("some text")
        ledger = OrphanLedger(records=(), push_orphan_ids=frozenset({"pid-1"}))
        decision = OrphanTriage().triage(ledger, model)

        assert "pid-1" in decision.excluded_ids


# =============================================================================
# OrphanTriage.triage — text NOT in new document
# =============================================================================


class TestOrphanTriagePreserved:
    def test_orphan_not_in_new_doc_is_preserved(self):
        model = _make_new_model("completely different content")
        record = _make_record(original_anchor_text="preserved forever")
        ledger = OrphanLedger(records=(record,))

        decision = OrphanTriage().triage(ledger, model)

        assert len(decision.recovered) == 0
        assert len(decision.preserved) == 1
        assert decision.preserved[0].annotation_id == "anno-1"

    def test_preserved_id_is_in_excluded_ids(self):
        model = _make_new_model("completely different content")
        record = _make_record(annotation_id="p1", original_anchor_text="lost text")
        ledger = OrphanLedger(records=(record,))

        decision = OrphanTriage().triage(ledger, model)

        assert "p1" in decision.excluded_ids

    def test_excluded_ids_is_union_of_preserved_and_push_orphans(self):
        model = _make_new_model("completely different content")
        record = _make_record(annotation_id="p1", original_anchor_text="lost text")
        ledger = OrphanLedger(records=(record,), push_orphan_ids=frozenset({"push-id"}))

        decision = OrphanTriage().triage(ledger, model)

        assert decision.excluded_ids == frozenset({"p1", "push-id"})

    def test_orphan_without_anchor_text_is_preserved(self):
        model = _make_new_model("preserved forever lives here")
        record = _make_record(original_anchor_text=None)
        ledger = OrphanLedger(records=(record,))

        decision = OrphanTriage().triage(ledger, model)

        assert len(decision.preserved) == 1
        assert len(decision.recovered) == 0


# =============================================================================
# OrphanTriage.triage — text IS in new document
# =============================================================================


class TestOrphanTriageRecovered:
    def test_orphan_found_in_new_doc_is_recovered(self):
        model = _make_new_model("The phrase preserved forever has been restored here.")
        record = _make_record(
            original_anchor_text="preserved forever",
            blocks_blob=_minimal_blob(),
        )
        ledger = OrphanLedger(records=(record,))

        decision = OrphanTriage().triage(ledger, model)

        assert len(decision.recovered) == 1
        assert len(decision.preserved) == 0

    def test_recovered_annotation_has_correct_id(self):
        model = _make_new_model("preserved forever is back")
        record = _make_record(annotation_id="ann-42", original_anchor_text="preserved forever")
        ledger = OrphanLedger(records=(record,))

        decision = OrphanTriage().triage(ledger, model)

        assert decision.recovered[0].annotation.annotation_id == "ann-42"

    def test_recovered_annotation_has_original_rm_block(self):
        model = _make_new_model("preserved forever is back")
        record = _make_record(original_anchor_text="preserved forever")
        ledger = OrphanLedger(records=(record,))

        decision = OrphanTriage().triage(ledger, model)

        assert decision.recovered[0].annotation.original_rm_block is not None

    def test_recovered_id_is_not_in_excluded_ids(self):
        model = _make_new_model("preserved forever is back")
        record = _make_record(annotation_id="r1", original_anchor_text="preserved forever")
        ledger = OrphanLedger(records=(record,))

        decision = OrphanTriage().triage(ledger, model)

        assert "r1" not in decision.excluded_ids


# =============================================================================
# OrphanTriage.triage — stroke type stays preserved
# =============================================================================


class TestOrphanTriageStrokes:
    def test_stroke_orphan_is_preserved_even_if_text_in_doc(self):
        model = _make_new_model("stroked text is back in the document")
        record = _make_record(
            annotation_type="stroke",
            original_anchor_text="stroked text",
        )
        ledger = OrphanLedger(records=(record,))

        decision = OrphanTriage().triage(ledger, model)

        assert len(decision.preserved) == 1
        assert len(decision.recovered) == 0
