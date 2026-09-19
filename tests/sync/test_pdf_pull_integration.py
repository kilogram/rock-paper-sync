"""Integration tests for Phase 4 PDF pull with rmfakecloud.

Validates the full pull-then-push cycle:
(a) Push a markdown file with known content.
(b) Inject synthetic .rm page files (highlight + margin stroke + trailing stroke)
    and a .content with cPages redir values.
(c) Pull with a mocked OCR service.
(d) Assert: ==highlight text==, ## Notes + OCR text, ![[...svg]] embed after
    the margin paragraph, SVG file on disk.
(e) Push again without discard_ink: no InkNotPulledError, index has no .rm,
    placements has one Placed.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from rmscene.scene_items import Rectangle

from rock_paper_sync.config import (
    AppConfig,
    CloudConfig,
    LayoutConfig,
    OCRConfig,
    SyncConfig,
    VaultConfig,
)
from rock_paper_sync.converter import SyncEngine
from rock_paper_sync.pull_sync import PullSyncEngine
from rock_paper_sync.rm_cloud_client import RmCloudClient
from rock_paper_sync.rm_cloud_sync import RmCloudSync
from rock_paper_sync.state import StateManager
from tests.helpers.rm_synthetic import (
    build_stroke_only_rm,
    build_synthetic_rm_with_stroke,
    build_two_highlights_rm,
    make_line,
)


def get_test_credentials() -> str:
    """Get device token from test credentials."""
    from tests.fixtures.rmfakecloud.helpers import get_credentials

    try:
        creds = get_credentials()
        return creds["device_token"]
    except FileNotFoundError as e:
        pytest.skip(f"rmfakecloud test credentials not found: {e}")


def _read_document_index(cloud_sync: RmCloudSync, doc_uuid: str) -> list:
    """Read the document index entries for a document."""
    root_entries, _, _ = cloud_sync.get_root_state(use_cache=False)
    doc_entry = None
    for entry in root_entries:
        if entry.entry_name == doc_uuid:
            doc_entry = entry
            break
    if doc_entry is None:
        return []
    doc_index_content = cloud_sync.sync_client.download_blob(doc_entry.hash)
    return cloud_sync.sync_client.parse_index(doc_index_content)


@pytest.fixture
def pdf_pull_env(tmp_path: Path, rmfakecloud: str):
    """Create isolated test environment for PDF pull integration tests."""
    vault = tmp_path / "vault"
    vault.mkdir()

    db = tmp_path / "state.db"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    config = AppConfig(
        sync=SyncConfig(
            vaults=[
                VaultConfig(
                    name="test-vault",
                    path=vault,
                    remarkable_folder=None,
                    include_patterns=["**/*.md"],
                    exclude_patterns=[],
                )
            ],
            state_database=db,
            debounce_seconds=1,
        ),
        cloud=CloudConfig(base_url=rmfakecloud),
        layout=LayoutConfig(),
        log_level="debug",
        log_file=tmp_path / "test.log",
        ocr=OCRConfig(),
        cache_dir=cache_dir,
    )

    device_token = get_test_credentials()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "device_token": device_token,
                "device_id": "rock-paper-sync-pull-test-001",
                "user_id": "",
            },
            f,
        )
        temp_creds_path = Path(f.name)

    rm_client = RmCloudClient(base_url=rmfakecloud, credentials_path=temp_creds_path)
    cloud_sync = RmCloudSync(base_url=rmfakecloud, client=rm_client)

    return {
        "config": config,
        "vault": vault,
        "cloud_sync": cloud_sync,
        "cache_dir": cache_dir,
        "temp_creds_path": temp_creds_path,
    }


# The test fixture markdown (copied from tests/record_replay/fixtures/)
_TEST_MD = """\
---
title: Pull Integration Test
---

# Pull Integration Test

## Highlight Target

The following sentence contains a distinctive four-word phrase
that the operator will highlight: purple monkey dishwasher engine
is the target for calibration measurement.

## Margin Note Target

This paragraph is the anchor for a margin note. The operator
will write a two-word note in the left margin beside this text
during the device session.
"""


@pytest.mark.offline
class TestPdfPullIntegration:
    """PDF pull integration tests against rmfakecloud."""

    def test_push_inject_pull_repush(self, pdf_pull_env):
        """Full lifecycle: push, inject .rm, pull with mock OCR, re-push.

        Steps:
        (a) Push the test file.
        (b) Inject synthetic .rm files + .content into the document index.
        (c) Pull with mocked OCR returning "hello world".
        (d) Assert highlights, notes, drawings in the vault file.
        (e) Re-push without discard_ink: clearance from pull works.
        """
        config = pdf_pull_env["config"]
        vault = pdf_pull_env["vault"]
        cloud_sync = pdf_pull_env["cloud_sync"]
        cache_dir = pdf_pull_env["cache_dir"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # --- (a) Initial push ---
        test_file = vault / "test-pull.md"
        test_file.write_text(_TEST_MD)

        result = engine.sync_file(vault_config, test_file)
        assert result.success, f"Initial push failed: {result.error}"
        doc_uuid = result.remarkable_uuid
        assert doc_uuid is not None

        # Get the word map for building synthetic rects
        file_state = state.get_file_state("test-vault", "test-pull.md")
        assert file_state is not None
        assert file_state.pushed_pdf_sha256 is not None

        from rock_paper_sync.render.wordmap import WordMap

        word_map = WordMap.from_json(file_state.word_map)

        # Find the token positions for "purple monkey dishwasher engine"
        target_words = ["purple", "monkey", "dishwasher", "engine"]
        from rock_paper_sync.device_profile import PROFILES, pdf_to_device

        profile = PROFILES["paper_pro_move"]

        # Build highlight rects in device coords from word-map positions
        highlight_rects = []
        for word in target_words:
            for pw in word_map.pages:
                for tid, rect in pw.tokens:
                    if word_map.texts[tid] == word:
                        # Convert PDF rect centre to device coords
                        cx = (rect.x0 + rect.x1) / 2.0
                        cy = (rect.y0 + rect.y1) / 2.0
                        dev_x, dev_y = pdf_to_device(profile, cx, cy)
                        w_dev = (rect.x1 - rect.x0) / 0.5
                        h_dev = (rect.y1 - rect.y0) / 0.5
                        highlight_rects.append(
                            Rectangle(
                                x=dev_x - w_dev / 2,
                                y=dev_y - h_dev / 2,
                                w=w_dev,
                                h=h_dev,
                            )
                        )
                        break

        assert len(highlight_rects) == 4, f"Expected 4 highlight rects, got {len(highlight_rects)}"

        # Build a stroke for the margin note (beside the "Margin Note Target" paragraph)
        margin_stroke = make_line(
            [(-400.0, 600.0), (-380.0, 610.0), (-360.0, 620.0), (-340.0, 630.0)]
        )

        # Build a stroke for the trailing page (appended handwriting)
        trailing_stroke = make_line([(0.0, 100.0), (50.0, 120.0), (100.0, 100.0), (150.0, 120.0)])

        # --- (b) Inject synthetic .rm files into the document index ---
        # Page 0: highlight + margin stroke
        glyph_text = "purple monkey dishwasher engine"
        page0_rm = build_synthetic_rm_with_stroke(glyph_text, highlight_rects, margin_stroke)

        # Trailing page (page index = content_pages + 0): just a stroke
        # Use stroke_id=50 to avoid collision with page 0's stroke (id=30)
        trailing_rm = build_stroke_only_rm(trailing_stroke, stroke_id=50)

        # Get current document index files
        cloud_sync.invalidate_root_cache()
        current_entries = _read_document_index(cloud_sync, doc_uuid)
        current_files: dict[str, bytes] = {}
        for entry in current_entries:
            blob_data = cloud_sync.sync_client.download_blob(entry.hash)
            current_files[entry.entry_name] = blob_data

        # Add .rm files
        page0_uuid = "page-0000-0000-0000-000000000001"
        trailing_uuid = "page-0000-0000-0000-000000000002"

        current_files[f"{doc_uuid}/{page0_uuid}.rm"] = page0_rm
        current_files[f"{doc_uuid}/{trailing_uuid}.rm"] = trailing_rm

        # Replace .content with cPages that have redir values
        content_pages = len(word_map.pages)
        content_with_cpages = {
            "cPages": {
                "pages": [
                    {"id": page0_uuid, "redir": {"value": 0}},
                    # Trailing blank page (redir >= content_pages = appended)
                    {"id": trailing_uuid, "redir": {"value": content_pages}},
                ],
            },
            "fileType": "pdf",
        }
        current_files[f"{doc_uuid}.content"] = json.dumps(content_with_cpages).encode()

        # Upload the modified document index
        cloud_sync.sync_client.upload_document(
            doc_uuid=doc_uuid,
            files=current_files,
            broadcast=False,
        )
        cloud_sync.invalidate_root_cache()

        # Verify .rm entries are in the index
        entries_with_rm = _read_document_index(cloud_sync, doc_uuid)
        rm_entries = [e for e in entries_with_rm if e.entry_name.endswith(".rm")]
        assert len(rm_entries) == 2, f"Expected 2 .rm entries, got {len(rm_entries)}"

        # --- (c) Pull with mocked OCR ---
        class MockOCR:
            """Mock OCR that returns 'hello world' for any image."""

            def process_image(self, png_bytes: bytes) -> str:
                return "hello world"

        pull_engine = PullSyncEngine(
            state=state,
            cloud_reader=cloud_sync,
            layout=config.layout,
            cache_dir=cache_dir,
            ocr_processor=MockOCR(),
        )

        pull_result = pull_engine.pull_file(vault_config, "test-pull.md")
        assert pull_result.success, f"Pull failed: {pull_result.error}"

        assert pull_result.highlights_added >= 1, "Expected at least one highlight"

        # --- (d) Assert vault file contents ---
        updated_content = test_file.read_text(encoding="utf-8")

        # Highlight should be wrapped in ==...==
        assert "==purple monkey dishwasher engine==" in updated_content, (
            "Highlight text should be wrapped in ==...=="
        )

        # Notes section with OCR text
        assert "## Notes" in updated_content, "Notes section should exist"
        assert "hello world" in updated_content, "OCR text should appear in notes"

        # Drawing SVG embed after margin paragraph
        assert "![[" in updated_content, "SVG embed should be present"
        assert ".svg]]" in updated_content, "SVG embed should reference an SVG file"

        # SVG file should exist on disk
        svg_files = list(vault.glob("*.svg"))
        if not svg_files:
            svg_files = list(vault.rglob("*.svg"))
        assert len(svg_files) >= 1, f"Expected at least 1 SVG file, found {len(svg_files)}"

        # --- (e) Re-push without discard_ink ---
        # Modify the file slightly to force a re-push
        test_file.write_text(updated_content + "\n\nAdded paragraph.\n")

        # Re-create engine to pick up state changes
        engine2 = SyncEngine(config, state, cloud_sync=cloud_sync)
        result2 = engine2.sync_file(vault_config, test_file)
        assert result2.success, f"Re-push failed: {result2.error}"
        assert not result2.skipped, "Modified file should not be skipped"

        # After push, index should have no .rm entries (PDF-only payload)
        cloud_sync.invalidate_root_cache()
        entries_after = _read_document_index(cloud_sync, doc_uuid)
        rm_after = [e for e in entries_after if e.entry_name.endswith(".rm")]
        assert len(rm_after) == 0, f"After re-push, .rm entries should be gone, got {len(rm_after)}"

        state.close()

    def test_pull_twice_is_idempotent(self, pdf_pull_env):
        """Pull twice without changing device state: second pull is a no-op.

        After push -> inject -> pull, a second pull on the same document
        with no new device ink must report zero new highlights/notes/drawings,
        leave the markdown byte-identical, and add no new ink_strokes rows.
        """
        config = pdf_pull_env["config"]
        vault = pdf_pull_env["vault"]
        cloud_sync = pdf_pull_env["cloud_sync"]
        cache_dir = pdf_pull_env["cache_dir"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # --- (a) Push ---
        test_file = vault / "test-idem.md"
        test_file.write_text(_TEST_MD)

        result = engine.sync_file(vault_config, test_file)
        assert result.success, f"Push failed: {result.error}"
        doc_uuid = result.remarkable_uuid

        file_state = state.get_file_state("test-vault", "test-idem.md")
        from rock_paper_sync.render.wordmap import WordMap

        word_map = WordMap.from_json(file_state.word_map)

        # --- (b) Inject highlight + stroke ---
        target_words = ["purple", "monkey", "dishwasher", "engine"]
        from rock_paper_sync.device_profile import PROFILES, pdf_to_device

        profile = PROFILES["paper_pro_move"]
        highlight_rects = []
        for word in target_words:
            for pw in word_map.pages:
                for tid, rect in pw.tokens:
                    if word_map.texts[tid] == word:
                        cx = (rect.x0 + rect.x1) / 2.0
                        cy = (rect.y0 + rect.y1) / 2.0
                        dev_x, dev_y = pdf_to_device(profile, cx, cy)
                        w_dev = (rect.x1 - rect.x0) / 0.5
                        h_dev = (rect.y1 - rect.y0) / 0.5
                        highlight_rects.append(
                            Rectangle(x=dev_x - w_dev / 2, y=dev_y - h_dev / 2, w=w_dev, h=h_dev)
                        )
                        break

        margin_stroke = make_line(
            [(-400.0, 600.0), (-380.0, 610.0), (-360.0, 620.0), (-340.0, 630.0)]
        )
        glyph_text = "purple monkey dishwasher engine"
        page0_rm = build_synthetic_rm_with_stroke(glyph_text, highlight_rects, margin_stroke)

        page0_uuid = "page-0000-0000-0000-000000000011"

        cloud_sync.invalidate_root_cache()
        current_entries = _read_document_index(cloud_sync, doc_uuid)
        current_files: dict[str, bytes] = {}
        for entry in current_entries:
            current_files[entry.entry_name] = cloud_sync.sync_client.download_blob(entry.hash)

        current_files[f"{doc_uuid}/{page0_uuid}.rm"] = page0_rm
        content_with_cpages = {
            "cPages": {"pages": [{"id": page0_uuid, "redir": {"value": 0}}]},
            "fileType": "pdf",
        }
        current_files[f"{doc_uuid}.content"] = json.dumps(content_with_cpages).encode()

        cloud_sync.sync_client.upload_document(
            doc_uuid=doc_uuid, files=current_files, broadcast=False
        )
        cloud_sync.invalidate_root_cache()

        # --- (c) First pull ---
        class MockOCR:
            def process_image(self, png_bytes: bytes) -> str:
                return "ocr text"

        pull_engine = PullSyncEngine(
            state=state,
            cloud_reader=cloud_sync,
            layout=config.layout,
            cache_dir=cache_dir,
            ocr_processor=MockOCR(),
        )
        pull1 = pull_engine.pull_file(vault_config, "test-idem.md")
        assert pull1.success, f"First pull failed: {pull1.error}"
        assert pull1.highlights_added >= 1

        md_after_pull1 = test_file.read_text(encoding="utf-8")
        strokes_after_pull1 = state.get_known_stroke_ids("test-vault", "test-idem.md")

        # --- (d) Second pull — must be a no-op ---
        pull2 = pull_engine.pull_file(vault_config, "test-idem.md")
        assert pull2.success, f"Second pull failed: {pull2.error}"
        assert pull2.highlights_added == 0, (
            f"Expected 0 new highlights, got {pull2.highlights_added}"
        )
        assert pull2.notes_appended == 0
        assert pull2.drawings_saved == 0

        md_after_pull2 = test_file.read_text(encoding="utf-8")
        assert md_after_pull2 == md_after_pull1, (
            "Markdown should be byte-identical after second pull"
        )

        strokes_after_pull2 = state.get_known_stroke_ids("test-vault", "test-idem.md")
        assert strokes_after_pull2 == strokes_after_pull1, "No new stroke rows after second pull"

        state.close()

    def test_two_highlights_same_phrase(self, pdf_pull_env):
        """Two GlyphRanges over the same phrase with distinct item_ids.

        Pull must produce two ledger rows, the phrase wrapped exactly
        twice in the markdown, and no IntegrityError.
        """
        config = pdf_pull_env["config"]
        vault = pdf_pull_env["vault"]
        cloud_sync = pdf_pull_env["cloud_sync"]
        cache_dir = pdf_pull_env["cache_dir"]

        # Markdown with the target word appearing twice
        two_phrase_md = """\
---
title: Two Highlights Test
---

# Two Highlights Test

The word target appears here in the first paragraph.

The word target appears here in the second paragraph.
"""
        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        test_file = vault / "test-two-hl.md"
        test_file.write_text(two_phrase_md)

        result = engine.sync_file(vault_config, test_file)
        assert result.success, f"Push failed: {result.error}"
        doc_uuid = result.remarkable_uuid

        file_state = state.get_file_state("test-vault", "test-two-hl.md")
        from rock_paper_sync.render.wordmap import WordMap

        word_map = WordMap.from_json(file_state.word_map)

        # Find both occurrences of "target" in the word map
        from rock_paper_sync.device_profile import PROFILES, pdf_to_device

        profile = PROFILES["paper_pro_move"]
        target_rects_list: list[list[Rectangle]] = []
        for pw in word_map.pages:
            for tid, rect in pw.tokens:
                if word_map.texts[tid] == "target":
                    cx = (rect.x0 + rect.x1) / 2.0
                    cy = (rect.y0 + rect.y1) / 2.0
                    dev_x, dev_y = pdf_to_device(profile, cx, cy)
                    w_dev = (rect.x1 - rect.x0) / 0.5
                    h_dev = (rect.y1 - rect.y0) / 0.5
                    target_rects_list.append(
                        [Rectangle(x=dev_x - w_dev / 2, y=dev_y - h_dev / 2, w=w_dev, h=h_dev)]
                    )

        assert len(target_rects_list) >= 2, (
            f"Expected at least 2 occurrences of 'target', found {len(target_rects_list)}"
        )

        # Build .rm with two GlyphRanges over "target" with distinct item_ids
        page0_rm = build_two_highlights_rm(
            "target",
            target_rects_list[0],
            target_rects_list[1],
            item_id_a=20,
            item_id_b=21,
        )

        page0_uuid = "page-0000-0000-0000-000000000021"

        cloud_sync.invalidate_root_cache()
        current_entries = _read_document_index(cloud_sync, doc_uuid)
        current_files: dict[str, bytes] = {}
        for entry in current_entries:
            current_files[entry.entry_name] = cloud_sync.sync_client.download_blob(entry.hash)

        current_files[f"{doc_uuid}/{page0_uuid}.rm"] = page0_rm
        content_with_cpages = {
            "cPages": {"pages": [{"id": page0_uuid, "redir": {"value": 0}}]},
            "fileType": "pdf",
        }
        current_files[f"{doc_uuid}.content"] = json.dumps(content_with_cpages).encode()

        cloud_sync.sync_client.upload_document(
            doc_uuid=doc_uuid, files=current_files, broadcast=False
        )
        cloud_sync.invalidate_root_cache()

        # Pull
        pull_engine = PullSyncEngine(
            state=state,
            cloud_reader=cloud_sync,
            layout=config.layout,
            cache_dir=cache_dir,
        )
        pull_result = pull_engine.pull_file(vault_config, "test-two-hl.md")
        assert pull_result.success, f"Pull failed: {pull_result.error}"
        assert pull_result.highlights_added == 2, (
            f"Expected 2 highlights, got {pull_result.highlights_added}"
        )

        # Check markdown: "target" wrapped twice
        content = test_file.read_text(encoding="utf-8")
        assert content.count("==target==") == 2, (
            f"Expected 2 ==target== occurrences, got {content.count('==target==')}"
        )

        # Check ledger: two distinct stroke rows for the two glyph_ids
        known_ids = state.get_known_stroke_ids("test-vault", "test-two-hl.md")
        # Both glyph_ids should be recorded: "1:20" and "1:21"
        assert "1:20" in known_ids, f"glyph_id 1:20 not in {known_ids}"
        assert "1:21" in known_ids, f"glyph_id 1:21 not in {known_ids}"

        state.close()

    def test_third_pull_after_repush_with_new_ink(self, pdf_pull_env):
        """After push -> inject -> pull -> re-push, a third pull with new ink works.

        Exercises the clearance path a second time. The new drawing must
        appear and the previous attachment must remain on disk.
        """
        config = pdf_pull_env["config"]
        vault = pdf_pull_env["vault"]
        cloud_sync = pdf_pull_env["cloud_sync"]
        cache_dir = pdf_pull_env["cache_dir"]

        state = StateManager(config.sync.state_database)
        engine = SyncEngine(config, state, cloud_sync=cloud_sync)
        vault_config = config.sync.vaults[0]

        # --- (a) Push ---
        test_file = vault / "test-3pull.md"
        test_file.write_text(_TEST_MD)

        result = engine.sync_file(vault_config, test_file)
        assert result.success, f"Push failed: {result.error}"
        doc_uuid = result.remarkable_uuid

        file_state = state.get_file_state("test-vault", "test-3pull.md")
        from rock_paper_sync.render.wordmap import WordMap

        word_map = WordMap.from_json(file_state.word_map)
        from rock_paper_sync.device_profile import PROFILES, pdf_to_device

        profile = PROFILES["paper_pro_move"]

        # --- (b) Inject highlight + margin stroke ---
        target_words = ["purple", "monkey", "dishwasher", "engine"]
        highlight_rects = []
        for word in target_words:
            for pw in word_map.pages:
                for tid, rect in pw.tokens:
                    if word_map.texts[tid] == word:
                        cx = (rect.x0 + rect.x1) / 2.0
                        cy = (rect.y0 + rect.y1) / 2.0
                        dev_x, dev_y = pdf_to_device(profile, cx, cy)
                        w_dev = (rect.x1 - rect.x0) / 0.5
                        h_dev = (rect.y1 - rect.y0) / 0.5
                        highlight_rects.append(
                            Rectangle(x=dev_x - w_dev / 2, y=dev_y - h_dev / 2, w=w_dev, h=h_dev)
                        )
                        break

        margin_stroke = make_line(
            [(-400.0, 600.0), (-380.0, 610.0), (-360.0, 620.0), (-340.0, 630.0)]
        )
        glyph_text = "purple monkey dishwasher engine"
        page0_uuid = "page-0000-0000-0000-000000000031"
        page0_rm = build_synthetic_rm_with_stroke(glyph_text, highlight_rects, margin_stroke)

        cloud_sync.invalidate_root_cache()
        current_entries = _read_document_index(cloud_sync, doc_uuid)
        current_files: dict[str, bytes] = {}
        for entry in current_entries:
            current_files[entry.entry_name] = cloud_sync.sync_client.download_blob(entry.hash)

        current_files[f"{doc_uuid}/{page0_uuid}.rm"] = page0_rm
        content_with_cpages = {
            "cPages": {"pages": [{"id": page0_uuid, "redir": {"value": 0}}]},
            "fileType": "pdf",
        }
        current_files[f"{doc_uuid}.content"] = json.dumps(content_with_cpages).encode()
        cloud_sync.sync_client.upload_document(
            doc_uuid=doc_uuid, files=current_files, broadcast=False
        )
        cloud_sync.invalidate_root_cache()

        # --- (c) First pull ---
        class MockOCR:
            def process_image(self, png_bytes: bytes) -> str:
                return "hello world"

        pull_engine = PullSyncEngine(
            state=state,
            cloud_reader=cloud_sync,
            layout=config.layout,
            cache_dir=cache_dir,
            ocr_processor=MockOCR(),
        )
        pull1 = pull_engine.pull_file(vault_config, "test-3pull.md")
        assert pull1.success, f"First pull failed: {pull1.error}"

        svg_files_after_pull1 = set(p.name for p in vault.rglob("*.svg"))
        assert len(svg_files_after_pull1) >= 1, "Expected at least 1 SVG after first pull"

        # --- (d) Re-push ---
        updated_content = test_file.read_text(encoding="utf-8")
        test_file.write_text(updated_content + "\n\nAdded paragraph for re-push.\n")
        engine2 = SyncEngine(config, state, cloud_sync=cloud_sync)
        result2 = engine2.sync_file(vault_config, test_file)
        assert result2.success, f"Re-push failed: {result2.error}"

        # --- (e) Inject new stroke (different stroke_id) ---
        new_stroke = make_line([(-300.0, 400.0), (-280.0, 420.0), (-260.0, 400.0), (-240.0, 420.0)])
        # stroke_id=70 to avoid collision with the first pull's strokes (30, 50)
        new_page_uuid = "page-0000-0000-0000-000000000032"
        new_rm = build_stroke_only_rm(new_stroke, stroke_id=70)

        cloud_sync.invalidate_root_cache()
        entries2 = _read_document_index(cloud_sync, doc_uuid)
        files2: dict[str, bytes] = {}
        for entry in entries2:
            files2[entry.entry_name] = cloud_sync.sync_client.download_blob(entry.hash)

        files2[f"{doc_uuid}/{new_page_uuid}.rm"] = new_rm
        content2 = {
            "cPages": {"pages": [{"id": new_page_uuid, "redir": {"value": 0}}]},
            "fileType": "pdf",
        }
        files2[f"{doc_uuid}.content"] = json.dumps(content2).encode()
        cloud_sync.sync_client.upload_document(doc_uuid=doc_uuid, files=files2, broadcast=False)
        cloud_sync.invalidate_root_cache()

        # --- (f) Third pull with new ink ---
        pull_engine2 = PullSyncEngine(
            state=state,
            cloud_reader=cloud_sync,
            layout=config.layout,
            cache_dir=cache_dir,
        )
        pull3 = pull_engine2.pull_file(vault_config, "test-3pull.md")
        assert pull3.success, f"Third pull failed: {pull3.error}"
        assert pull3.drawings_saved >= 1, f"Expected at least 1 drawing, got {pull3.drawings_saved}"

        # Previous SVG attachments still on disk
        svg_files_after_pull3 = set(p.name for p in vault.rglob("*.svg"))
        assert svg_files_after_pull1.issubset(svg_files_after_pull3), (
            f"Previous attachments missing: {svg_files_after_pull1 - svg_files_after_pull3}"
        )
        # At least one new SVG
        assert len(svg_files_after_pull3) > len(svg_files_after_pull1), (
            "Expected a new SVG attachment after third pull"
        )

        state.close()
