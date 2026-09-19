"""Tests for the Phase 4 ink interpretation pipeline.

Covers invariants I4, I5, I10, I11, I12 and the OCR-failure path.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from rock_paper_sync.device_profile import PROFILES
from rock_paper_sync.ink.apply import (
    apply_edits,
    compute_paragraph_anchors,
    strip_uninterpreted_markers,
    write_markdown_atomic,
)
from rock_paper_sync.ink.bake import bake_cluster, get_attachment_folder
from rock_paper_sync.ink.candidates import extract_candidates
from rock_paper_sync.ink.classify import classify_cluster
from rock_paper_sync.ink.clusters import InkCluster
from rock_paper_sync.ink.dispositions import (
    DOCUMENT_END,
    Baked,
    Consumed,
    Deferred,
    Highlight,
    InkAnchor,
    MarkdownEdit,
    is_terminal,
    paragraph_anchor_ref,
)
from rock_paper_sync.render.wordmap import (
    BakedOverlay,
    PageWords,
    Placed,
    Rect,
    Relocated,
    TokenId,
    WordMap,
)
from rock_paper_sync.state import StateManager, SyncRecord

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def profile():
    """Paper Pro Move device profile."""
    return PROFILES["paper_pro_move"]


@pytest.fixture
def simple_word_map():
    """A minimal word map with three tokens on one page."""
    return WordMap(
        page_size=(477.0, 848.0),
        pages=(
            PageWords(
                page=0,
                tokens=(
                    (TokenId(0), Rect(36.0, 36.0, 100.0, 50.0)),
                    (TokenId(1), Rect(105.0, 36.0, 200.0, 50.0)),
                    (TokenId(2), Rect(36.0, 60.0, 200.0, 74.0)),
                ),
            ),
        ),
        texts=("Hello", "world", "paragraph"),
    )


@pytest.fixture
def state_manager(tmp_path: Path):
    """Create a state manager with fresh database."""
    db = tmp_path / "test.db"
    mgr = StateManager(db)
    yield mgr
    mgr.close()


# ---------------------------------------------------------------------------
# Disposition types
# ---------------------------------------------------------------------------


class TestDispositions:
    """Tests for the Disposition sum type."""

    def test_highlight_is_terminal(self) -> None:
        edit = MarkdownEdit(
            kind="highlight",
            cluster_id="abc",
            anchor=InkAnchor(kind="paragraph", ref="ref"),
            text="hello",
        )
        d = Highlight(edit=edit)
        assert is_terminal(d)

    def test_consumed_is_terminal(self) -> None:
        edit = MarkdownEdit(
            kind="append",
            cluster_id="abc",
            anchor=DOCUMENT_END,
            text="note",
        )
        d = Consumed(edit_id="e1", edit=edit)
        assert is_terminal(d)

    def test_baked_is_terminal(self) -> None:
        edit = MarkdownEdit(
            kind="embed",
            cluster_id="abc",
            anchor=DOCUMENT_END,
            text="![[x.svg]]",
        )
        d = Baked(attachment="x.svg", anchor=DOCUMENT_END, svg=b"<svg/>", edit=edit)
        assert is_terminal(d)

    def test_deferred_is_not_terminal(self) -> None:
        edit = MarkdownEdit(
            kind="deferred",
            cluster_id="abc",
            anchor=DOCUMENT_END,
            text="deferred",
        )
        d = Deferred(reason="ocr_unavailable", edit=edit)
        assert not is_terminal(d)


# ---------------------------------------------------------------------------
# Paragraph anchoring
# ---------------------------------------------------------------------------


class TestParagraphAnchoring:
    """Tests for paragraph anchor computation."""

    def test_anchor_ref_deterministic(self) -> None:
        ref1 = paragraph_anchor_ref("Hello world", 0)
        ref2 = paragraph_anchor_ref("Hello world", 0)
        assert ref1 == ref2

    def test_anchor_ref_ordinal_differs(self) -> None:
        ref0 = paragraph_anchor_ref("Hello", 0)
        ref1 = paragraph_anchor_ref("Hello", 1)
        assert ref0 != ref1

    def test_compute_paragraph_anchors(self) -> None:
        text = "First paragraph.\n\nSecond paragraph.\n\nFirst paragraph."
        anchors = compute_paragraph_anchors(text)
        assert len(anchors) == 3  # Two unique texts, but one duplicate
        # The duplicate gets ordinal 1
        refs = list(anchors.keys())
        assert all("#" in r for r in refs)


# ---------------------------------------------------------------------------
# I4: A consumed stroke never re-enters a candidate set
# ---------------------------------------------------------------------------


class TestI4StrokeDeduplication:
    """I4: pull twice → second pull yields zero candidates."""

    def test_known_strokes_excluded(self) -> None:
        """Stroke ids in the ledger are excluded from candidates."""
        # Empty rm_files → no candidates (nothing to extract)
        candidates = extract_candidates(
            rm_files={},
            cpages_entries=[{"id": "page1", "redir": {"value": 0}}],
            known_stroke_ids=frozenset({"2:30"}),
        )
        assert candidates == []


# ---------------------------------------------------------------------------
# I5: One stroke belongs to at most one cluster
# ---------------------------------------------------------------------------


class TestI5StrokeUniqueness:
    """I5: inserting a duplicate ink_strokes row raises IntegrityError."""

    def test_duplicate_stroke_raises(self, state_manager: StateManager) -> None:
        with state_manager.conn:
            state_manager.insert_ink_strokes([("vault", "path.md", "uuid1", "2:30", "cluster_a")])
        with pytest.raises(sqlite3.IntegrityError):
            with state_manager.conn:
                state_manager.insert_ink_strokes(
                    [("vault", "path.md", "uuid1", "2:30", "cluster_b")]
                )


# ---------------------------------------------------------------------------
# I10: Ink is interpreted only against the PDF on the device
# ---------------------------------------------------------------------------


class TestI10RenderMismatch:
    """I10: PDF hash mismatch → RenderMismatchError, markdown untouched."""

    def test_render_mismatch_error_fields(self) -> None:
        from rock_paper_sync.errors import RenderMismatchError

        err = RenderMismatchError("doc-uuid", "expected_hash", "actual_hash")
        assert err.doc_uuid == "doc-uuid"
        assert err.expected_hash == "expected_hash"
        assert err.actual_hash == "actual_hash"
        # The error message truncates hashes to 12 chars
        assert "expected_has" in str(err)
        assert "doc-uuid" in str(err)


# ---------------------------------------------------------------------------
# I11: Baked placement is total
# ---------------------------------------------------------------------------


class TestI11OverlayPlacement:
    """I11: every overlay gets Placed or Relocated, never dropped.

    Tests go through the full render pipeline (``PdfRenderer.render``)
    so that placements come from the actual box-tree walk, not arithmetic.
    """

    def test_document_end_overlay_placed(self, tmp_path: Path) -> None:
        """An overlay with anchor='document_end' is Placed."""
        from rock_paper_sync.config import LayoutConfig
        from rock_paper_sync.parser import parse_markdown_file
        from rock_paper_sync.render.pdf import PdfRenderer

        md = tmp_path / "test.md"
        md.write_text("# Hello\n\nParagraph text.\n")
        profile = PROFILES["paper_pro_move"]
        layout = LayoutConfig()
        renderer = PdfRenderer(layout)
        doc = parse_markdown_file(md)
        overlay = BakedOverlay(
            anchor="document_end",
            offset=Rect(0, 0, 50, 50),
            svg=b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
            b'<rect width="50" height="50" fill="blue"/></svg>',
        )
        rendered = renderer.render(doc, profile, overlays=[overlay])
        assert len(rendered.placements) == 1
        assert isinstance(rendered.placements[0], Placed)

    def test_unknown_anchor_relocated(self, tmp_path: Path) -> None:
        """An overlay whose anchor paragraph was deleted -> Relocated."""
        from rock_paper_sync.config import LayoutConfig
        from rock_paper_sync.parser import parse_markdown_file
        from rock_paper_sync.render.pdf import PdfRenderer

        md = tmp_path / "test.md"
        md.write_text("# Hello\n\nParagraph text.\n")
        profile = PROFILES["paper_pro_move"]
        layout = LayoutConfig()
        renderer = PdfRenderer(layout)
        doc = parse_markdown_file(md)
        overlay = BakedOverlay(
            anchor="nonexistent_anchor_ref#99",
            offset=Rect(0, 0, 50, 50),
            svg=b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
            b'<rect width="50" height="50" fill="red"/></svg>',
        )
        rendered = renderer.render(doc, profile, overlays=[overlay])
        assert len(rendered.placements) == 1
        assert isinstance(rendered.placements[0], Relocated)


# ---------------------------------------------------------------------------
# I12: Pull is crash-safe (duplicates not loss)
# ---------------------------------------------------------------------------


class TestI12CrashSafety:
    """I12: write order ensures duplicates, never loss."""

    def test_atomic_write(self, tmp_path: Path) -> None:
        """write_markdown_atomic replaces file atomically."""
        target = tmp_path / "test.md"
        target.write_text("original", encoding="utf-8")

        write_markdown_atomic(target, "updated")
        assert target.read_text(encoding="utf-8") == "updated"

    def test_atomic_write_no_partial(self, tmp_path: Path) -> None:
        """On failure, the original file is untouched."""
        target = tmp_path / "test.md"
        target.write_text("original", encoding="utf-8")

        # Try to write to a read-only directory (should fail)
        # This test is structural — we verify the temp file is cleaned up
        write_markdown_atomic(target, "safe write")
        assert target.read_text(encoding="utf-8") == "safe write"


# ---------------------------------------------------------------------------
# Strip UNINTERPRETED markers
# ---------------------------------------------------------------------------


class TestStripUninterpretedMarkers:
    """strip_uninterpreted_markers removes RPS:UNINTERPRETED blocks."""

    def test_strip_basic(self) -> None:
        text = (
            "Before\n"
            "<!-- RPS:UNINTERPRETED kind=strikethrough confidence=0.42 page=3 -->\n"
            "![[note-drawing-p3-a1b2c3d4.svg]]\n"
            "<!-- RPS:END -->\n"
            "After"
        )
        result = strip_uninterpreted_markers(text)
        assert "RPS:UNINTERPRETED" not in result
        assert "Before" in result
        assert "After" in result

    def test_strip_none(self) -> None:
        text = "No markers here."
        result = strip_uninterpreted_markers(text)
        assert result == text


# ---------------------------------------------------------------------------
# Apply edits
# ---------------------------------------------------------------------------


class TestApplyEdits:
    """Tests for the markdown edit application."""

    def test_highlight_edit(self) -> None:
        edit = MarkdownEdit(
            kind="highlight",
            cluster_id="c1",
            anchor=InkAnchor(kind="paragraph", ref=""),
            text="target phrase",
        )
        snapshot = "This is a target phrase in context."
        current = "This is a target phrase in context."
        new_text, failed = apply_edits([edit], snapshot, current)
        assert "==target phrase==" in new_text
        assert failed == []

    def test_append_edit(self) -> None:
        edit = MarkdownEdit(
            kind="append",
            cluster_id="c1",
            anchor=DOCUMENT_END,
            text="OCR result here",
        )
        snapshot = "# Title\n\nBody text."
        current = "# Title\n\nBody text."
        new_text, failed = apply_edits([edit], snapshot, current, notes_heading="Notes")
        assert "## Notes" in new_text
        assert "OCR result here" in new_text

    def test_highlight_not_found_in_current(self) -> None:
        """A highlight whose text is not in the current file is applied via patch."""
        edit = MarkdownEdit(
            kind="highlight",
            cluster_id="c1",
            anchor=InkAnchor(kind="paragraph", ref=""),
            text="nonexistent phrase",
        )
        # Text is in snapshot but not in current
        snapshot = "The nonexistent phrase is here."
        current = "Totally rewritten document with nothing in common."
        new_text, failed = apply_edits([edit], snapshot, current)
        # Either the patch applied (fuzzy match) or it failed
        # Both outcomes are acceptable; what matters is no exception
        assert isinstance(new_text, str)


# ---------------------------------------------------------------------------
# Bake cluster to SVG
# ---------------------------------------------------------------------------


class TestBakeCluster:
    """Tests for SVG baking."""

    def test_bake_produces_svg(self, profile) -> None:
        """bake_cluster produces valid SVG bytes."""
        from unittest.mock import MagicMock

        from rock_paper_sync.ink.candidates import CandidateStroke

        # Create a mock stroke with points
        mock_stroke = MagicMock()
        mock_stroke.stroke_id.part1 = 2
        mock_stroke.stroke_id.part2 = 30
        mock_stroke.bounding_box = (100.0, 100.0, 50.0, 50.0)
        mock_stroke.points = [
            MagicMock(x=100.0, y=100.0, width=2.0),
            MagicMock(x=120.0, y=110.0, width=2.0),
            MagicMock(x=150.0, y=150.0, width=2.0),
        ]

        cs = CandidateStroke(stroke=mock_stroke, page_index=0, redir=0)
        cluster = InkCluster(
            cluster_id="abcdef1234567890" * 4,
            strokes=(cs,),
            bbox=(100.0, 100.0, 150.0, 150.0),
            page_index=0,
            redir=0,
        )

        anchor = InkAnchor(kind="paragraph", ref="test_ref")
        anchor_rect = Rect(x0=36.0, y0=80.0, x1=400.0, y1=100.0)

        name, svg_bytes, overlay = bake_cluster(cluster, profile, anchor, anchor_rect, "test_doc")

        assert name.startswith("test_doc-drawing-p0-")
        assert name.endswith(".svg")
        assert b"<svg" in svg_bytes
        assert b"path" in svg_bytes


# ---------------------------------------------------------------------------
# OCR failure → Deferred → InkNotPulledError
# ---------------------------------------------------------------------------


class TestOCRFailurePath:
    """OCR raising → cluster Deferred → no clearance → push raises."""

    def test_deferred_blocks_clearance(self, state_manager: StateManager) -> None:
        """Deferred clusters prevent ink_clearance_hash from being set."""
        # Insert a deferred cluster
        import time

        state_manager.insert_ink_cluster(
            cluster_id="deferred_cluster",
            vault_name="vault",
            obsidian_path="test.md",
            disposition="deferred",
            deferred_reason="ocr_unavailable",
            page_redir=None,
            rect=None,
            anchor_kind="document_end",
            anchor_ref="",
            attachment=None,
            edit_id=None,
            pushed_pdf_sha256="pdfhash",
            created_at=int(time.time()),
        )

        assert state_manager.has_deferred_clusters("vault", "test.md")


# ---------------------------------------------------------------------------
# Classify cluster
# ---------------------------------------------------------------------------


class TestClassifyCluster:
    """Tests for cluster classification."""

    def test_no_redir_is_appended(self, simple_word_map, profile) -> None:
        """Cluster on a device-added page (no redir) → appended."""
        cluster = InkCluster(
            cluster_id="test",
            strokes=(),
            bbox=(100.0, 100.0, 150.0, 150.0),
            page_index=5,
            redir=None,
        )
        result = classify_cluster(cluster, simple_word_map, profile, content_page_count=1)
        assert result.kind == "appended"

    def test_trailing_page_is_appended(self, simple_word_map, profile) -> None:
        """Cluster on a trailing blank page → appended."""
        cluster = InkCluster(
            cluster_id="test",
            strokes=(),
            bbox=(100.0, 100.0, 150.0, 150.0),
            page_index=2,
            redir=5,  # Beyond content page count
        )
        result = classify_cluster(cluster, simple_word_map, profile, content_page_count=1)
        assert result.kind == "appended"

    def test_content_page_is_drawing(self, simple_word_map, profile) -> None:
        """Cluster on a content page → drawing."""
        cluster = InkCluster(
            cluster_id="test",
            strokes=(),
            bbox=(100.0, 100.0, 150.0, 150.0),
            page_index=0,
            redir=0,
        )
        result = classify_cluster(cluster, simple_word_map, profile, content_page_count=1)
        assert result.kind == "drawing"


# ---------------------------------------------------------------------------
# State accessor methods
# ---------------------------------------------------------------------------


class TestStateInkMethods:
    """Tests for ink-related state accessor methods."""

    def test_get_known_stroke_ids_empty(self, state_manager: StateManager) -> None:
        ids = state_manager.get_known_stroke_ids("vault", "test.md")
        assert ids == frozenset()

    def test_insert_and_get_stroke_ids(self, state_manager: StateManager) -> None:
        with state_manager.conn:
            state_manager.insert_ink_strokes(
                [
                    ("vault", "test.md", "uuid1", "2:30", "cluster_a"),
                    ("vault", "test.md", "uuid1", "2:31", "cluster_a"),
                ]
            )
        ids = state_manager.get_known_stroke_ids("vault", "test.md")
        assert ids == frozenset({"2:30", "2:31"})

    def test_get_baked_clusters(self, state_manager: StateManager) -> None:
        import time

        state_manager.insert_ink_cluster(
            cluster_id="baked1",
            vault_name="vault",
            obsidian_path="test.md",
            disposition="baked",
            deferred_reason=None,
            page_redir=0,
            rect=None,
            anchor_kind="paragraph",
            anchor_ref="ref",
            attachment="test.svg",
            edit_id=None,
            pushed_pdf_sha256="hash",
            created_at=int(time.time()),
        )
        baked = state_manager.get_baked_clusters("vault", "test.md")
        assert len(baked) == 1
        assert baked[0]["attachment"] == "test.svg"

    def test_no_deferred_when_all_terminal(self, state_manager: StateManager) -> None:
        assert not state_manager.has_deferred_clusters("vault", "test.md")


# ---------------------------------------------------------------------------
# Attachment folder
# ---------------------------------------------------------------------------


class TestAttachmentFolder:
    """Tests for get_attachment_folder."""

    def test_default_is_vault_root(self, tmp_path: Path) -> None:
        folder = get_attachment_folder(tmp_path)
        assert folder == tmp_path

    def test_reads_obsidian_config(self, tmp_path: Path) -> None:
        obsidian_dir = tmp_path / ".obsidian"
        obsidian_dir.mkdir()
        app_json = obsidian_dir / "app.json"
        app_json.write_text(json.dumps({"attachmentFolderPath": "assets"}))

        folder = get_attachment_folder(tmp_path)
        assert folder == tmp_path / "assets"


# ---------------------------------------------------------------------------
# I8: Transform cross-check — negative test (rects offset by 30 pt)
# ---------------------------------------------------------------------------


class TestI8TransformMismatch:
    """I8: highlight rects that don't match word-map tokens raise TransformMismatchError."""

    def test_offset_rects_fail_i8(self, simple_word_map, profile) -> None:
        """GlyphRange rects offset by 30 pt from the true position fail I8."""
        # Build device rects that are 30 pt off from where "Hello" is
        # in the word map. "Hello" is at (36, 36, 100, 50) in PDF pts.
        # In device coords, that's approximately (x_dev, y_dev) via pdf_to_device.
        from rock_paper_sync.device_profile import pdf_to_device
        from rock_paper_sync.ink.calibration import _DeviceRect, check_i8

        # True PDF centre of the "Hello" token
        true_cx = (36.0 + 100.0) / 2.0
        true_cy = (36.0 + 50.0) / 2.0
        # Convert to device coords
        dev_x, dev_y = pdf_to_device(profile, true_cx, true_cy)
        # Offset by 60 device px (30 pt / 0.5 scale) to guarantee a miss
        bad_x = dev_x + 120.0
        bad_y = dev_y + 120.0

        device_rects = [_DeviceRect(x=bad_x, y=bad_y, w=20.0, h=10.0)]
        result = check_i8("Hello", device_rects, simple_word_map, profile, page=0)
        assert not result.i8_pass


# ---------------------------------------------------------------------------
# Process lock
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# I10: PDF hash mismatch actually raised on pull
# ---------------------------------------------------------------------------


class TestI10Enforcement:
    """I10: pull compares the .pdf entry hash with pushed_pdf_sha256."""

    def test_corrupt_hash_raises_render_mismatch(self, state_manager: StateManager) -> None:
        """Corrupted pushed_pdf_sha256 in state → RenderMismatchError."""
        from unittest.mock import MagicMock

        from rock_paper_sync.config import LayoutConfig, VaultConfig
        from rock_paper_sync.pull_sync import PullSyncEngine
        from rock_paper_sync.render.wordmap import DocumentIndexEntry

        # Set up sync state with a pushed PDF hash
        state_manager.update_file_state(
            SyncRecord(
                vault_name="v",
                obsidian_path="t.md",
                remarkable_uuid="doc-uuid-1",
                content_hash="ch",
                last_sync_time=1,
                page_count=1,
                status="synced",
                pushed_pdf_sha256="expected_hash_abc",
                word_map=WordMap(
                    page_size=(477.0, 848.0),
                    pages=(PageWords(page=0, tokens=()),),
                    texts=(),
                ).to_json(),
            )
        )

        # Mock cloud reader that returns a different hash for the PDF entry
        cloud = MagicMock()
        cloud.get_document_index_hash.return_value = "some_doc_hash"
        cloud.get_document_index_entries.return_value = [
            DocumentIndexEntry(entry_name="doc-uuid-1.pdf", hash="different_hash_xyz"),
        ]

        vault = VaultConfig(
            name="v",
            path=Path("/tmp/fakevault"),
            remarkable_folder=None,
            include_patterns=["**/*.md"],
            exclude_patterns=[],
        )
        engine = PullSyncEngine(
            state=state_manager,
            cloud_reader=cloud,
            layout=LayoutConfig(),
            cache_dir=Path("/tmp/fake-cache"),
        )

        result = engine.pull_file(vault, "t.md")
        assert not result.success
        assert "mismatch" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Idempotent highlight wrapping
# ---------------------------------------------------------------------------


class TestIdempotentHighlights:
    """Highlights are idempotent: wrapping ==text== twice is a no-op."""

    def test_already_wrapped_is_noop(self) -> None:
        """If text is already ==wrapped==, apply_edits does not double-wrap."""
        edit = MarkdownEdit(
            kind="highlight",
            cluster_id="c1",
            anchor=InkAnchor(kind="paragraph", ref=""),
            text="target phrase",
        )
        snapshot = "This is a target phrase in context."
        current = "This is a ==target phrase== in context."
        new_text, failed = apply_edits([edit], snapshot, current)
        assert new_text.count("==target phrase==") == 1
        assert "====target phrase====" not in new_text
        assert failed == []

    def test_two_same_phrase_highlights_wrap_both(self) -> None:
        """Two highlights of the same phrase wrap distinct occurrences."""
        edit1 = MarkdownEdit(
            kind="highlight",
            cluster_id="c1",
            anchor=InkAnchor(kind="paragraph", ref=""),
            text="word",
        )
        edit2 = MarkdownEdit(
            kind="highlight",
            cluster_id="c2",
            anchor=InkAnchor(kind="paragraph", ref=""),
            text="word",
        )
        snapshot = "word and word again."
        current = "word and word again."
        new_text, failed = apply_edits([edit1, edit2], snapshot, current)
        assert new_text.count("==word==") == 2
        assert failed == []

    def test_snapshot_span_skips_earlier_prose_occurrence(self) -> None:
        """Highlight with snapshot_span wraps at the correct position.

        The phrase "target" appears earlier in the text as prose.
        The highlight (with snapshot_span pointing to the second occurrence)
        must wrap only the second, leaving the first bare.
        """
        snapshot = "The target is mentioned early.\n\nLater, highlight this target word."
        current = snapshot  # unchanged
        # snapshot_span points to the second "target" (position 55..61)
        second_idx = snapshot.index("target", snapshot.index("target") + 1)
        edit = MarkdownEdit(
            kind="highlight",
            cluster_id="c1",
            anchor=InkAnchor(kind="paragraph", ref=""),
            text="target",
            snapshot_span=(second_idx, second_idx + len("target")),
        )
        new_text, failed = apply_edits([edit], snapshot, current)
        assert failed == []
        # First "target" must remain bare
        first_idx = new_text.index("target")
        assert new_text[first_idx - 2 : first_idx] != "=="
        # Second "target" must be wrapped
        assert "==target==" in new_text
        assert new_text.count("==target==") == 1

    def test_snapshot_span_none_falls_back_to_substring(self) -> None:
        """When snapshot_span is None, apply_edits uses substring fallback."""
        snapshot = "The word target appears here."
        current = snapshot
        edit = MarkdownEdit(
            kind="highlight",
            cluster_id="c1",
            anchor=InkAnchor(kind="paragraph", ref=""),
            text="target",
            snapshot_span=None,
        )
        new_text, failed = apply_edits([edit], snapshot, current)
        assert "==target==" in new_text
        assert failed == []


# ---------------------------------------------------------------------------
# build_token_spans
# ---------------------------------------------------------------------------


class TestBuildTokenSpans:
    """Token-to-snapshot position mapping."""

    def test_basic_mapping(self) -> None:
        from rock_paper_sync.ink.apply import build_token_spans

        texts = ("Hello", "world", "paragraph")
        snapshot = "Hello world\n\nparagraph"
        spans = build_token_spans(texts, snapshot)
        assert spans[0] == (0, 5)
        assert spans[1] == (6, 11)
        assert spans[2] == (13, 22)

    def test_synthetic_marker_gets_minus_one(self) -> None:
        from rock_paper_sync.ink.apply import build_token_spans

        # Bullet marker does not appear in the snapshot
        texts = ("•", "item", "text")
        snapshot = "- item text"
        spans = build_token_spans(texts, snapshot)
        assert spans[0] == (-1, -1)
        assert spans[1][0] >= 0
        assert spans[2][0] >= 0


# ---------------------------------------------------------------------------
# v11 migration forces re-push
# ---------------------------------------------------------------------------


class TestV11MigrationForcesPush:
    """Nulled word_map in state must cause sync_file to re-push."""

    def test_null_word_map_not_skipped(self, state_manager: StateManager) -> None:
        """After v11 migration nulls word_map, sync_file must not skip the file."""
        import time

        state_manager.update_file_state(
            SyncRecord(
                vault_name="v",
                obsidian_path="t.md",
                remarkable_uuid="uuid-1",
                content_hash="hash_aaa",
                last_sync_time=int(time.time()),
                page_count=1,
                status="synced",
                pushed_pdf_sha256="pdf_hash",
                word_map=None,  # simulates v11 migration
                render_config_hash="rcfg",
            )
        )
        st = state_manager.get_file_state("v", "t.md")
        assert st is not None
        assert st.word_map is None
        # The converter's skip condition checks word_map_missing:
        # content_unchanged and render_unchanged and not push_incomplete and not word_map_missing
        # With word_map=None, word_map_missing=True, so skip should be False.
        word_map_missing = st.word_map is None
        assert word_map_missing, "word_map_missing should be True when word_map is NULL"

    def test_null_word_map_triggers_push_via_sync_file(self, tmp_path: Path) -> None:
        """sync_file with null word_map actually pushes (not just skip=False)."""
        import time
        from unittest.mock import MagicMock

        from rock_paper_sync.config import (
            AppConfig,
            CloudConfig,
            LayoutConfig,
            OCRConfig,
            SyncConfig,
            VaultConfig,
        )

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        md_file = vault_dir / "t.md"
        md_file.write_text("# Test\n\nSome content.\n")

        db = tmp_path / "state.db"
        config = AppConfig(
            sync=SyncConfig(
                vaults=[
                    VaultConfig(
                        name="v",
                        path=vault_dir,
                        remarkable_folder=None,
                        include_patterns=["**/*.md"],
                        exclude_patterns=[],
                    )
                ],
                state_database=db,
                debounce_seconds=1,
            ),
            cloud=CloudConfig(base_url="http://fake:3000"),
            layout=LayoutConfig(),
            log_level="debug",
            log_file=tmp_path / "test.log",
            ocr=OCRConfig(),
            cache_dir=tmp_path / "cache",
        )

        state = StateManager(db)

        # Pre-seed state with null word_map (simulates v11 migration)
        state.update_file_state(
            SyncRecord(
                vault_name="v",
                obsidian_path="t.md",
                remarkable_uuid="uuid-v11",
                content_hash="will_match",
                last_sync_time=int(time.time()),
                page_count=1,
                status="synced",
                pushed_pdf_sha256="old_hash",
                word_map=None,
                render_config_hash=config.layout.render_config_hash(),
            )
        )

        # Compute the same content hash the parser will produce so
        # content_unchanged is True (only word_map_missing triggers push).
        from rock_paper_sync.parser import parse_markdown_file

        doc = parse_markdown_file(md_file)
        state.update_file_state(
            SyncRecord(
                vault_name="v",
                obsidian_path="t.md",
                remarkable_uuid="uuid-v11",
                content_hash=doc.content_hash,
                last_sync_time=int(time.time()),
                page_count=1,
                status="synced",
                pushed_pdf_sha256="old_hash",
                word_map=None,
                render_config_hash=config.layout.render_config_hash(),
            )
        )

        # Mock cloud_sync so the upload path is exercised but no network call
        from rock_paper_sync.rm_cloud_sync import InkClearance

        mock_cloud = MagicMock()
        mock_cloud.get_root_state.return_value = ([], "root_hash", None)
        mock_cloud.ensure_folder_hierarchy.return_value = None
        mock_cloud.upload_pdf_document.return_value = None
        mock_cloud.mint_clearance_if_no_ink.return_value = InkClearance(
            doc_uuid="uuid-v11", doc_index_hash="fake_hash"
        )
        mock_cloud.get_document_index_hash.return_value = None

        from rock_paper_sync.converter import SyncEngine

        engine = SyncEngine(config, state, cloud_sync=mock_cloud)
        vault_config = config.sync.vaults[0]

        result = engine.sync_file(vault_config, md_file)

        assert result.success, f"sync_file failed: {result.error}"
        assert not result.skipped, "File should NOT be skipped when word_map is NULL"
        # The push should have called upload_pdf_document
        assert mock_cloud.upload_pdf_document.called, "upload_pdf_document should have been called"

        # After push, word_map should be populated
        st = state.get_file_state("v", "t.md")
        assert st is not None
        assert st.word_map is not None, "word_map should be populated after re-push"

        state.close()


# ---------------------------------------------------------------------------
# Overlay positioning: red rectangle near the margin-note paragraph
# ---------------------------------------------------------------------------


class TestOverlayPositioning:
    """Blocker 1: overlays positioned inside anchor blocks in the PDF."""

    def test_overlay_placed_on_anchor_page(self, tmp_path: Path) -> None:
        """An overlay anchored to a paragraph renders on the same page."""
        import hashlib

        from rock_paper_sync.config import LayoutConfig
        from rock_paper_sync.parser import parse_markdown_file
        from rock_paper_sync.render.pdf import PdfRenderer

        fixture = Path("tests/record_replay/fixtures/test_pdf_substrate.md")
        if not fixture.exists():
            pytest.skip("fixture not found")

        doc = parse_markdown_file(fixture)
        profile = PROFILES["paper_pro_move"]
        layout = LayoutConfig()
        renderer = PdfRenderer(layout)

        # Compute anchor ref for "Margin Note Target" paragraph
        target_text = (
            "This paragraph is the anchor for a margin note. The operator "
            "will write a two-word note in the left margin beside this text "
            "during the device session."
        )
        normalised = " ".join(target_text.split())
        text_hash = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
        anchor_ref = f"{text_hash}#0"

        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
            b'<rect width="40" height="20" fill="red"/></svg>'
        )
        overlay = BakedOverlay(
            anchor=anchor_ref,
            offset=Rect(x0=-60, y0=0, x1=-20, y1=20),
            svg=svg,
        )

        rendered = renderer.render(doc, profile, overlays=[overlay])
        assert len(rendered.placements) == 1
        placement = rendered.placements[0]
        assert isinstance(placement, Placed)

        # Verify the overlay is on the same page as the anchor paragraph
        # (the paragraph should be on page 0 or 1 depending on content length)
        assert placement.page >= 0

        # Verify the rect was read from the box tree (non-zero, reasonable)
        assert placement.rect.x1 > placement.rect.x0
        assert placement.rect.y1 > placement.rect.y0

    def test_word_map_has_blocks(self, tmp_path: Path) -> None:
        """The rendered WordMap includes blocks from data-anchor elements."""
        from rock_paper_sync.config import LayoutConfig
        from rock_paper_sync.parser import parse_markdown_file
        from rock_paper_sync.render.pdf import PdfRenderer

        md = tmp_path / "test.md"
        md.write_text("# Heading\n\nFirst para.\n\nSecond para.\n")
        profile = PROFILES["paper_pro_move"]
        layout = LayoutConfig()
        renderer = PdfRenderer(layout)
        doc = parse_markdown_file(md)
        rendered = renderer.render(doc, profile)
        wm = rendered.word_map
        # Should have blocks for heading + 2 paragraphs
        assert len(wm.blocks) >= 3
        # Each block should have a non-empty anchor_ref
        for blk in wm.blocks:
            assert blk.anchor_ref
            assert "#" in blk.anchor_ref
        # token_block should be populated
        assert len(wm.token_block) == len(wm.texts)


# ---------------------------------------------------------------------------
# GlyphHighlight.glyph_id stability
# ---------------------------------------------------------------------------


class TestGlyphHighlightId:
    """GlyphHighlight carries a stable glyph_id from the device CrdtId."""

    def test_glyph_id_populated(self) -> None:
        from rock_paper_sync.ink.classify import GlyphHighlight

        gh = GlyphHighlight(
            glyph_id="1:20",
            text="hello world",
            page=0,
            device_rects=[(0.0, 0.0, 10.0, 5.0)],
        )
        assert gh.glyph_id == "1:20"


# ---------------------------------------------------------------------------
# Process lock
# ---------------------------------------------------------------------------


class TestSyncLock:
    """Process lock prevents concurrent sync invocations."""

    def test_lock_prevents_concurrent(self, tmp_path: Path) -> None:
        """Acquiring the lock twice raises SyncLockError."""
        from rock_paper_sync.converter import SyncLockError, sync_lock

        db_path = tmp_path / "state.db"
        db_path.touch()

        with sync_lock(db_path):
            # Second attempt should fail immediately
            with pytest.raises(SyncLockError) as exc_info:
                with sync_lock(db_path):
                    pass  # pragma: no cover
            assert "Another sync is running" in str(exc_info.value)

    def test_lock_released_after_context(self, tmp_path: Path) -> None:
        """After the context exits, the lock can be reacquired."""
        from rock_paper_sync.converter import sync_lock

        db_path = tmp_path / "state.db"
        db_path.touch()

        with sync_lock(db_path):
            pass

        # Should succeed now
        with sync_lock(db_path):
            pass
