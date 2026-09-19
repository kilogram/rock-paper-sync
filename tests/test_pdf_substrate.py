"""Tests for the PDF substrate (Phase 1).

Covers invariants I1, I2, I6, I7 (via test_device_profile_guard.py),
I9, and the integration smoke test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rock_paper_sync.config import LayoutConfig
from rock_paper_sync.device_profile import (
    PROFILES,
    DeviceProfile,
    device_to_pdf,
    pdf_to_device,
)
from rock_paper_sync.render.pdf import PdfRenderer
from rock_paper_sync.render.wordmap import (
    PageWords,
    Rect,
    RenderedDocument,
    TokenId,
    WordMap,
)
from rock_paper_sync.rm_cloud_sync import (
    InkClearance,
    InkNotPulledError,
    PdfDocumentPayload,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def profile() -> DeviceProfile:
    return PROFILES["paper_pro_move"]


@pytest.fixture
def layout() -> LayoutConfig:
    return LayoutConfig()


@pytest.fixture
def renderer(layout: LayoutConfig) -> PdfRenderer:
    return PdfRenderer(layout)


@pytest.fixture
def sample_markdown_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "sample_markdown"


def _render_fixture(
    renderer: PdfRenderer,
    profile: DeviceProfile,
    fixture_dir: Path,
    filename: str,
) -> RenderedDocument:
    from rock_paper_sync.parser import parse_markdown_file

    doc = parse_markdown_file(fixture_dir / filename)
    return renderer.render(doc, profile)


# ---------------------------------------------------------------------------
# I1: Every parser token has >= 1 box, contiguous in reading order
# ---------------------------------------------------------------------------


class TestI1TokenBoxes:
    """I1: every token has at least one laid-out box."""

    @pytest.mark.parametrize(
        "filename",
        [
            "simple.md",
            "headers.md",
            "lists.md",
            "code_blocks.md",
            "formatting.md",
            "frontmatter.md",
            "complex.md",
            "comprehensive.md",
            "edge_cases.md",
            "long.md",
            "highlights.md",
            "long_token.md",
        ],
    )
    def test_all_tokens_have_boxes(
        self,
        renderer: PdfRenderer,
        profile: DeviceProfile,
        sample_markdown_dir: Path,
        filename: str,
    ) -> None:
        """Every token in every fixture has at least one box."""
        result = _render_fixture(renderer, profile, sample_markdown_dir, filename)
        wm = result.word_map
        for i, text in enumerate(wm.texts):
            tid = TokenId(i)
            rects = wm.rects_of(tid)
            assert len(rects) >= 1, f"Token {i} ({text!r}) has no boxes"

    def test_long_unbroken_token_has_multiple_boxes(
        self,
        renderer: PdfRenderer,
        profile: DeviceProfile,
        sample_markdown_dir: Path,
    ) -> None:
        """A 300-char unbroken token must break across lines -> multiple boxes."""
        result = _render_fixture(renderer, profile, sample_markdown_dir, "long_token.md")
        wm = result.word_map
        # Find the long token
        long_idx = None
        for i, text in enumerate(wm.texts):
            if len(text) > 100:
                long_idx = i
                break
        assert long_idx is not None, "Long token not found in fixture"
        rects = wm.rects_of(TokenId(long_idx))
        assert len(rects) > 1, f"300-char token should break into multiple boxes, got {len(rects)}"

    def test_mark_highlight_tokens_present(
        self,
        renderer: PdfRenderer,
        profile: DeviceProfile,
        sample_markdown_dir: Path,
    ) -> None:
        """==text== renders correctly and tokens exist in the word map."""
        result = _render_fixture(renderer, profile, sample_markdown_dir, "highlights.md")
        wm = result.word_map
        assert "highlighted" in wm.texts
        assert "text" in wm.texts


# ---------------------------------------------------------------------------
# Rendering regression tests
# ---------------------------------------------------------------------------


class TestRenderingRegression:
    """Regression tests for HTML/CSS rendering correctness."""

    def test_ordered_list_html_has_numbering(self) -> None:
        """Nested ordered list HTML must contain '1.' and '2.' markers."""
        from rock_paper_sync.parser import parse_content
        from rock_paper_sync.render.html import ast_to_html

        blocks = parse_content("1. First\n2. Second\n   1. Sub A\n   2. Sub B\n3. Third\n")
        html, _ = ast_to_html(blocks)

        # Ordered markers must appear in the output
        assert ">1.<" in html, f"Expected '1.' marker in HTML:\n{html}"
        assert ">2.<" in html, f"Expected '2.' marker in HTML:\n{html}"
        assert ">3.<" in html, f"Expected '3.' marker in HTML:\n{html}"
        # Nested items restart at 1
        # Count occurrences of ">1.<" — should be at least 2 (top-level + nested)
        assert html.count(">1.<") >= 2, "Nested ordered list should restart numbering"

    def test_fenced_code_block_no_language_tag(self) -> None:
        """Fenced code block HTML must not contain the language tag as text."""
        from rock_paper_sync.parser import parse_content
        from rock_paper_sync.render.html import ast_to_html

        blocks = parse_content("```python\ndef hello():\n    pass\n```\n")
        html, _ = ast_to_html(blocks)

        # The language tag "[python]" must not appear
        assert "[python]" not in html, f"Language tag leaked into HTML:\n{html}"
        # The code itself must appear
        assert "def hello():" in html

    def test_strikethrough_renders_del(self) -> None:
        """~~text~~ must produce a <del> tag."""
        from rock_paper_sync.parser import parse_content
        from rock_paper_sync.render.html import ast_to_html

        blocks = parse_content("This has ~~struck~~ text.\n")
        html, _ = ast_to_html(blocks)
        assert "<del>" in html, f"Expected <del> in HTML:\n{html}"
        assert "struck" in html

    def test_link_renders_underlined_no_url(self) -> None:
        """Links render as underlined text without the URL."""
        from rock_paper_sync.parser import parse_content
        from rock_paper_sync.render.html import ast_to_html

        blocks = parse_content("[click here](https://example.com)\n")
        html, _ = ast_to_html(blocks)
        assert "click" in html
        assert "here" in html
        assert "example.com" not in html, f"URL should not appear in HTML:\n{html}"
        assert "rps-link" in html

    def test_image_placeholder_has_filename(self) -> None:
        """Image placeholders must include the filename."""
        from rock_paper_sync.parser import parse_content
        from rock_paper_sync.render.html import ast_to_html

        blocks = parse_content("![Alt text](./images/diagram.png)\n")
        html, _ = ast_to_html(blocks)
        assert "diagram.png" in html
        assert "[Image:" in html

    def test_table_renders_html_table(self) -> None:
        """Pipe table renders as <table> with header row bold and data cells."""
        from rock_paper_sync.parser import parse_content
        from rock_paper_sync.render.html import ast_to_html

        blocks = parse_content("| Name | Value |\n|------|-------|\n| alpha | 1 |\n| beta | 2 |\n")
        html, token_count = ast_to_html(blocks)

        assert "<table" in html, f"Expected <table> in HTML:\n{html}"
        assert "<th>" in html, "Header cells should use <th>"
        assert "<td>" in html, "Data cells should use <td>"
        assert "Name" in html
        assert "alpha" in html
        assert token_count >= 6  # 2 headers + 4 data cells

    def test_footnote_ref_renders_superscript(self) -> None:
        """Footnote reference [^1] renders as superscript number."""
        from rock_paper_sync.parser import parse_content
        from rock_paper_sync.render.html import ast_to_html

        blocks = parse_content("Text with footnote[^1].\n\n[^1]: The footnote.\n")
        html, _ = ast_to_html(blocks)

        assert "<sup>" in html, f"Expected <sup> for footnote ref in HTML:\n{html}"
        # The footnote definition should render as a numbered list item
        assert "footnote." in html
        # The definition marker "1." should appear
        assert ">1.<" in html


# ---------------------------------------------------------------------------
# I2: PdfDocumentPayload produces exactly four keys, no .rm
# ---------------------------------------------------------------------------


class TestI2PdfPayload:
    """I2: payload produces exactly {uuid}.metadata/content/pagedata/pdf."""

    def test_to_files_keys(self) -> None:
        uuid = "test-uuid-1234"
        payload = PdfDocumentPayload(
            doc_uuid=uuid,
            metadata=b'{"test": true}',
            content=b'{"fileType": "pdf"}',
            pagedata=b"",
            pdf=b"%PDF-1.4 fake",
        )
        files = payload.to_files()
        expected = {
            f"{uuid}.metadata",
            f"{uuid}.content",
            f"{uuid}.pagedata",
            f"{uuid}.pdf",
        }
        assert set(files.keys()) == expected

    def test_no_rm_in_payload(self) -> None:
        """No .rm file can appear in a PDF payload."""
        payload = PdfDocumentPayload(
            doc_uuid="uuid",
            metadata=b"{}",
            content=b"{}",
            pagedata=b"",
            pdf=b"%PDF",
        )
        files = payload.to_files()
        rm_keys = [k for k in files if k.endswith(".rm")]
        assert rm_keys == []

    def test_payload_is_frozen(self) -> None:
        payload = PdfDocumentPayload(
            doc_uuid="uuid",
            metadata=b"{}",
            content=b"{}",
            pagedata=b"",
            pdf=b"%PDF",
        )
        with pytest.raises(AttributeError):
            payload.doc_uuid = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# I6: coordinate round-trip within 0.5 px and aspect equality
# ---------------------------------------------------------------------------


class TestI6CoordinateRoundTrip:
    """I6: round-trip and aspect ratio."""

    def test_round_trip_origin(self) -> None:
        profile = PROFILES["paper_pro_move"]
        pdf_x, pdf_y = device_to_pdf(profile, 0.0, 0.0)
        dx, dy = pdf_to_device(profile, pdf_x, pdf_y)
        assert abs(dx) < 0.5
        assert abs(dy) < 0.5

    def test_round_trip_corners(self) -> None:
        profile = PROFILES["paper_pro_move"]
        w = profile.screen_w_px
        h = profile.screen_h_px
        for dev_x, dev_y in [(-w / 2, 0), (w / 2, 0), (0, h), (-w / 2, h), (w / 2, h)]:
            px, py = device_to_pdf(profile, dev_x, dev_y)
            rx, ry = pdf_to_device(profile, px, py)
            assert abs(rx - dev_x) < 0.5, f"x round-trip failed: {dev_x} -> {rx}"
            assert abs(ry - dev_y) < 0.5, f"y round-trip failed: {dev_y} -> {ry}"

    def test_aspect_ratio_preserved(self) -> None:
        profile = PROFILES["paper_pro_move"]
        device_aspect = profile.screen_w_px / profile.screen_h_px
        page_w, page_h = profile.page_size_pt
        page_aspect = page_w / page_h
        assert abs(device_aspect - page_aspect) < 1e-6


# ---------------------------------------------------------------------------
# I6 property test
# ---------------------------------------------------------------------------

try:
    from hypothesis import given
    from hypothesis import strategies as st

    @given(
        x=st.floats(min_value=-500, max_value=500, allow_nan=False),
        y=st.floats(min_value=0, max_value=2000, allow_nan=False),
    )
    @pytest.mark.property
    def test_coordinate_round_trip_property(x: float, y: float) -> None:
        """I6 property: round-trip within 0.5 px for any point."""
        profile = PROFILES["paper_pro_move"]
        pdf_x, pdf_y = device_to_pdf(profile, x, y)
        rx, ry = pdf_to_device(profile, pdf_x, pdf_y)
        assert abs(rx - x) < 0.5
        assert abs(ry - y) < 0.5

except ImportError:
    pass


# ---------------------------------------------------------------------------
# I9: InkClearance and InkNotPulledError
# ---------------------------------------------------------------------------


class TestI9InkClearance:
    """I9: push requires clearance; stale clearance raises."""

    def test_ink_not_pulled_error_attrs(self) -> None:
        err = InkNotPulledError("uuid-123", "has .rm files")
        assert err.doc_uuid == "uuid-123"
        assert "uuid-123" in str(err)
        assert "--discard-ink" in str(err)

    def test_clearance_is_frozen(self) -> None:
        c = InkClearance(doc_uuid="uuid", doc_index_hash="abc")
        with pytest.raises(AttributeError):
            c.doc_uuid = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# WordMap serialisation round-trip
# ---------------------------------------------------------------------------


class TestWordMapSerialization:
    def test_round_trip(self) -> None:
        wm = WordMap(
            page_size=(477.0, 848.0),
            pages=(_make_page(0, [(0, Rect(10, 20, 50, 30)), (1, Rect(60, 20, 100, 30))]),),
            texts=("hello", "world"),
        )
        data = wm.to_json()
        restored = WordMap.from_json(data)
        assert restored.page_size == wm.page_size
        assert len(restored.pages) == len(wm.pages)
        assert restored.texts == wm.texts

    def test_rects_of_raises_on_unknown(self) -> None:
        wm = WordMap(page_size=(100, 100), pages=(), texts=("a",))
        # Token 0 exists in texts but has no boxes
        with pytest.raises(KeyError):
            wm.rects_of(TokenId(0))

    def test_rects_of_raises_on_out_of_range(self) -> None:
        wm = WordMap(page_size=(100, 100), pages=(), texts=("a",))
        with pytest.raises(KeyError):
            wm.rects_of(TokenId(99))


def _make_page(page: int, items: list) -> PageWords:
    return PageWords(page=page, tokens=tuple((TokenId(tid), rect) for tid, rect in items))


# ---------------------------------------------------------------------------
# CloudReader structural subtyping
# ---------------------------------------------------------------------------


class TestCloudReaderProtocol:
    def test_rmcloudsync_satisfies_cloud_reader(self) -> None:
        from rock_paper_sync.cloud_reader import CloudReader
        from rock_paper_sync.rm_cloud_sync import RmCloudSync

        assert issubclass(RmCloudSync, CloudReader)


# ---------------------------------------------------------------------------
# Schema v9 migration
# ---------------------------------------------------------------------------


class TestSchemaV9:
    def test_fresh_db_has_v9_columns(self, tmp_path: Path) -> None:
        from rock_paper_sync.state import StateManager

        mgr = StateManager(tmp_path / "test.db")
        cursor = mgr.conn.execute("PRAGMA table_info(sync_state)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "pending_pdf_sha256" in columns
        assert "pushed_pdf_sha256" in columns
        assert "word_map" in columns
        assert "ink_clearance_hash" in columns
        assert "render_config_hash" in columns
        mgr.close()

    def test_v8_to_v9_migration(self, tmp_path: Path) -> None:
        """Migrating from v8 adds the new columns."""
        import sqlite3

        db_path = tmp_path / "migrate.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE sync_state (
                vault_name TEXT NOT NULL,
                obsidian_path TEXT NOT NULL,
                remarkable_uuid TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                last_sync_time INTEGER NOT NULL,
                page_count INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'synced',
                last_root_generation INTEGER,
                last_doc_index_hash TEXT,
                PRIMARY KEY (vault_name, obsidian_path)
            );
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            INSERT INTO schema_version (version) VALUES (8);
            CREATE TABLE folder_mapping (
                vault_name TEXT, obsidian_folder TEXT, remarkable_uuid TEXT,
                PRIMARY KEY (vault_name, obsidian_folder)
            );
            CREATE TABLE sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vault_name TEXT, obsidian_path TEXT,
                action TEXT, timestamp INTEGER, details TEXT
            );
            CREATE TABLE paragraph_state (
                vault_name TEXT, obsidian_path TEXT, paragraph_index INTEGER,
                content_hash TEXT, has_annotations BOOLEAN DEFAULT 0,
                annotation_count INTEGER DEFAULT 0, last_checked INTEGER,
                PRIMARY KEY (vault_name, obsidian_path, paragraph_index)
            );
            CREATE TABLE ocr_results (
                vault_name TEXT, obsidian_path TEXT, annotation_uuid TEXT,
                paragraph_index INTEGER, ocr_text TEXT, ocr_text_hash TEXT,
                original_text_hash TEXT, image_hash TEXT, confidence REAL,
                model_version TEXT, processed_time INTEGER,
                PRIMARY KEY (vault_name, obsidian_path, annotation_uuid)
            );
            CREATE TABLE ocr_corrections (
                id TEXT PRIMARY KEY, image_hash TEXT UNIQUE, image_path TEXT,
                original_text TEXT, corrected_text TEXT, paragraph_context TEXT,
                document_id TEXT, dataset_version TEXT, created_at INTEGER
            );
            CREATE TABLE pull_state (
                vault_name TEXT NOT NULL, obsidian_path TEXT NOT NULL,
                remarkable_uuid TEXT NOT NULL,
                last_annotation_hash TEXT, last_pull_time INTEGER,
                last_rm_files_hash TEXT,
                PRIMARY KEY (vault_name, obsidian_path)
            ) STRICT;
            CREATE TABLE orphaned_annotations (
                vault_name TEXT NOT NULL, obsidian_path TEXT NOT NULL,
                annotation_id TEXT NOT NULL, annotation_type TEXT NOT NULL,
                original_anchor_text TEXT, orphaned_at INTEGER NOT NULL,
                blocks_blob BLOB, source_page_idx INTEGER,
                PRIMARY KEY (vault_name, obsidian_path, annotation_id)
            ) STRICT;
        """)
        conn.close()

        from rock_paper_sync.state import StateManager

        mgr = StateManager(db_path)
        cursor = mgr.conn.execute("PRAGMA table_info(sync_state)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "pushed_pdf_sha256" in columns
        assert "render_config_hash" in columns

        # Check version is now 9
        cursor = mgr.conn.execute("SELECT version FROM schema_version")
        assert cursor.fetchone()[0] == 9
        mgr.close()


# ---------------------------------------------------------------------------
# LayoutConfig new fields
# ---------------------------------------------------------------------------


class TestLayoutConfig:
    def test_default_layout_config(self) -> None:
        lc = LayoutConfig()
        assert lc.font_size_pt == 11.0
        assert lc.line_height == 1.4
        assert lc.margin_top_pt == 36.0
        assert lc.trailing_blank_pages == 2
        assert lc.notes_heading == "Notes"

    def test_render_config_hash_changes(self) -> None:
        lc1 = LayoutConfig(font_size_pt=11.0)
        lc2 = LayoutConfig(font_size_pt=12.0)
        assert lc1.render_config_hash() != lc2.render_config_hash()

    def test_get_device_profile(self) -> None:
        lc = LayoutConfig(device="paper_pro_move")
        p = lc.get_device_profile()
        assert p.name == "paper_pro_move"

    def test_get_device_profile_invalid(self) -> None:
        from rock_paper_sync.config import ConfigError

        lc = LayoutConfig(device="nonexistent_device")
        with pytest.raises(ConfigError, match="Supported profiles"):
            lc.get_device_profile()
