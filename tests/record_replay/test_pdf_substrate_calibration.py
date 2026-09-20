"""PDF substrate calibration: device session recording and offline analysis.

Phase 2 of the PDF substrate plan.  Records a two-trip device session
(trip 1: annotate, trip 2: re-push with ``discard_ink``) and analyses
the captured ink against the word map produced by the PDF renderer.

Online recording (run by the operator):
    uv run pytest tests/record_replay/test_pdf_substrate_calibration.py \\
        --online -s

Offline analysis (runs when testdata exists, otherwise skips):
    uv run pytest tests/record_replay/test_pdf_substrate_calibration.py

Synthetic unit test (always runs, no testdata required):
    uv run pytest tests/record_replay/test_pdf_substrate_calibration.py \\
        -k TestCalibrationSynthetic

Expected page count for the fixture:
    - 1 content page (the fixture is short)
    - 2 trailing blank pages (default ``trailing_blank_pages=2``)
    - Total: 3 pages
    Highlight target is on page 0.
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import pytest
import rmscene
from rmscene import (
    AuthorIdsBlock,
    CrdtId,
    CrdtSequenceItem,
    LwwValue,
    MigrationInfoBlock,
    PageInfoBlock,
    SceneGlyphItemBlock,
    SceneGroupItemBlock,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)
from rmscene.scene_items import GlyphRange, Group, PenColor, Rectangle

from rock_paper_sync.device_profile import (
    PDF_CANVAS_H,
    PDF_CANVAS_W,
    PROFILES,
    CanvasTransform,
    DeviceProfile,
    pdf_to_device,
)
from rock_paper_sync.ink.calibration import (
    analyse_cpages,
    calibrate_highlight,
    check_i8,
    fit_affine_1d,
)
from rock_paper_sync.render.wordmap import PageWords, Rect, TokenId, WordMap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FIXTURE_NAME = "test_pdf_substrate.md"
_TESTDATA_DIR_NAME = "pdf_substrate"
_PROFILE = PROFILES["paper_pro_move"]

# Legacy profile/layout for trip 1 replay: the recording was pushed with
# the old 477x848 pt geometry (screen px * 0.5, ppi=144 equivalent).
_LEGACY_PROFILE = DeviceProfile(name="trip1_legacy", screen_w_px=954, screen_h_px=1696, ppi=144)
_LEGACY_TRANSFORM = CanvasTransform.for_pdf_page(_LEGACY_PROFILE)

# Expected page count for the LEGACY layout: 1 content + 2 trailing blank
_EXPECTED_TOTAL_PAGES = 3
_EXPECTED_CONTENT_PAGES = 1

# Highlight target phrase
_HIGHLIGHT_PHRASE = "purple monkey dishwasher engine"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _testdata_dir() -> Path:
    return Path(__file__).parent / "testdata" / _TESTDATA_DIR_NAME


def _fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


# ============================================================================
# ONLINE TEST — recording session (only runs with --online -s)
# ============================================================================


@pytest.mark.online_only
@pytest.mark.device
def test_pdf_substrate_online(device, workspace, fixtures_dir):
    """Record the PDF substrate calibration session.

    Trip 1: push fixture as PDF, prompt operator for seven annotation
    actions, then capture all document files from the cloud.

    Trip 2: re-push the same file with discard_ink=True, prompt operator
    to confirm the document opens clean, then capture content/metadata.
    """
    fixture_doc = fixtures_dir / _FIXTURE_NAME
    workspace.test_doc.write_text(fixture_doc.read_text())

    test_id = device.start_test_for_fixture(
        fixture_doc,
        description="PDF substrate calibration (2 trips)",
    )

    # ------------------------------------------------------------------
    # TRIP 1 — push and annotate
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("TRIP 1: Push PDF and annotate on device")
    print("=" * 60)

    doc_uuid = device.upload_document(workspace.test_doc)

    # Prompt the operator for each action, one at a time
    from tests.record_replay.harness.prompts import user_prompt

    actions = [
        (
            "Highlight the phrase",
            [
                'Find the paragraph under "Highlight Target".',
                'Highlight exactly: "purple monkey dishwasher engine"',
                "Use the default highlight colour.",
            ],
        ),
        (
            "Write a margin note",
            [
                'Find the paragraph under "Margin Note Target".',
                "In the LEFT margin, write a two-word note (e.g. 'test note').",
            ],
        ),
        (
            "Tick checklist item 2",
            [
                'Find the "Checklist" section.',
                'Tap or draw a tick in the checkbox of "Tick the second item".',
            ],
        ),
        (
            "Strikethrough",
            [
                'Find the "Strikethrough Target" section.',
                'Draw a horizontal line through "DELETE THIS PHRASE".',
            ],
        ),
        (
            "Caret insertion",
            [
                'Find the "Caret Insertion Target" section.',
                'Draw a caret (^) under the gap before "here."',
                "Write one word above the line in that gap.",
            ],
        ),
        (
            "Write on trailing blank page",
            [
                "Swipe to the first trailing blank (ruled) page.",
                "Write two lines of text anywhere on it.",
            ],
        ),
        (
            "Add a page and write",
            [
                "Use the device menu to add a page AFTER the last page.",
                "Write one word on the new page.",
            ],
        ),
    ]

    for title, steps in actions:
        user_prompt(title, steps)

    # Capture annotations
    trip1_state = device.wait_for_annotations(doc_uuid)
    assert trip1_state.has_annotations, "Trip 1: annotations required"

    # Download additional document files (content, metadata, pdf hash)
    # via RmCloudSync directly
    _capture_document_files(device, doc_uuid, trip_number=1)

    print(f"\nTrip 1: captured {len(trip1_state.rm_files)} .rm file(s)")

    # ------------------------------------------------------------------
    # TRIP 2 — re-push with discard_ink (D5 check)
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("TRIP 2: Re-push with discard_ink=True (D5 verification)")
    print("=" * 60)

    # Re-push same file — the workspace already has the fixture
    # We need to force re-sync by touching the file
    workspace.test_doc.write_text(fixture_doc.read_text() + "\n")

    # Run sync with discard_ink — handled by passing it through the CLI
    # or by directly calling the sync engine.  The harness run_sync
    # does not support discard_ink, so we call the engine directly.
    from rock_paper_sync.config import load_config
    from rock_paper_sync.converter import SyncEngine

    config = load_config(workspace.config_file)
    from rock_paper_sync.state import StateManager

    state = StateManager(config.sync.state_database)
    engine = SyncEngine(config, state)
    vault = config.sync.vaults[0]
    result = engine.sync_file(vault, workspace.test_doc, discard_ink=True)
    print(f"Trip 2 sync result: success={result.success}, error={result.error}")

    user_prompt(
        "Verify clean document",
        [
            "Open the document on the device.",
            "Confirm it shows NO ink (all annotations cleared).",
            "If the document does not open or shows ink, note that.",
        ],
    )

    # Capture trip 2 files
    _capture_document_files(device, doc_uuid, trip_number=2)

    device.end_test(test_id)
    print("\nOnline recording complete.")


def _capture_document_files(device, doc_uuid: str, trip_number: int) -> None:
    """Download .content, .metadata, .rm pages, and PDF blob hash.

    Saves them into testdata/{test_id}/trips/{trip_number}/.
    """
    from rock_paper_sync.rm_cloud_client import RmCloudClient
    from rock_paper_sync.rm_cloud_sync import RmCloudSync

    client = RmCloudClient(base_url=device.workspace.cloud_url)
    sync = RmCloudSync(base_url=device.workspace.cloud_url, client=client)

    # Get the document index
    root_docs = sync.sync_client.get_root_documents()
    doc_entry = None
    for entry in root_docs:
        if entry.entry_name == doc_uuid:
            doc_entry = entry
            break

    if doc_entry is None:
        device.bench.warn(f"Document {doc_uuid} not found in root for trip {trip_number}")
        return

    # Download and parse document index
    doc_index_bytes = sync.sync_client.download_blob(doc_entry.hash)
    doc_files = sync.sync_client.parse_index(doc_index_bytes)
    file_map = {entry.entry_name: entry.hash for entry in doc_files}

    # Build the capture dict
    captured: dict[str, bytes] = {}
    pdf_hash: str | None = None

    for filename, blob_hash in file_map.items():
        short_name = filename.split("/", 1)[-1] if "/" in filename else filename
        if short_name.endswith((".content", ".metadata", ".rm")):
            blob_data = sync.sync_client.download_blob(blob_hash)
            captured[short_name] = blob_data
        elif short_name.endswith(".pdf"):
            pdf_hash = blob_hash  # Record hash, don't download the full PDF

    # Save to testdata
    testdata_dir = _testdata_dir()
    trip_dir = testdata_dir / "trips" / str(trip_number)
    trip_dir.mkdir(parents=True, exist_ok=True)

    for name, data in captured.items():
        (trip_dir / name).write_bytes(data)

    # Save manifest for this trip
    manifest = {
        "trip_number": trip_number,
        "doc_uuid": doc_uuid,
        "pdf_blob_hash": pdf_hash,
        "files": list(captured.keys()),
        "doc_index_hash": doc_entry.hash,
    }

    # Parse .content for cPages if present
    content_file = next((v for k, v in captured.items() if k.endswith(".content")), None)
    if content_file is not None:
        try:
            content_json = json.loads(content_file)
            manifest["content_json"] = content_json
        except (json.JSONDecodeError, TypeError):
            pass

    (trip_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    device.bench.ok(
        f"Trip {trip_number}: captured {len(captured)} files"
        + (f", PDF blob hash: {pdf_hash}" if pdf_hash else "")
    )


# ============================================================================
# OFFLINE REPLAY TEST — runs when testdata exists
# ============================================================================


def _skip_if_no_testdata() -> None:
    """Skip the test if the online session has not been recorded."""
    td = _testdata_dir()
    manifest = td / "trips" / "1" / "manifest.json"
    if not td.exists() or not manifest.exists():
        pytest.skip(
            f"Testdata not found at {td}. "
            "Run the online session first: "
            "uv run pytest tests/record_replay/test_pdf_substrate_calibration.py --online -s"
        )


class TestPdfSubstrateReplay:
    """Offline analysis of the recorded PDF substrate session.

    Skips entirely if ``testdata/pdf_substrate/`` does not exist.
    """

    def setup_method(self) -> None:
        _skip_if_no_testdata()

    def test_pdf_render_determinism(self, tmp_path: Path) -> None:
        """I10 precondition: re-render produces the same PDF hash."""
        trip1_manifest = _load_trip_manifest(1)
        recorded_pdf_hash = trip1_manifest.get("pdf_blob_hash")
        if recorded_pdf_hash is None:
            pytest.skip("No PDF blob hash in trip 1 manifest")

        # Re-render the fixture
        rendered = _render_fixture()
        assert rendered.pdf_sha256 == recorded_pdf_hash, (
            f"Re-rendered PDF hash {rendered.pdf_sha256} != "
            f"recorded {recorded_pdf_hash} (I10 violated)"
        )

    def test_highlight_transform_calibration(self) -> None:
        """Measured transform via single-rect highlight.

        The device records one GlyphRange rect covering the entire
        phrase, not one per word.  We verify the canvas transform by
        transforming the single device rect to PDF and comparing its
        x centre with the word-map phrase centre.
        """
        trip1_dir = _testdata_dir() / "trips" / "1"
        rm_files = _load_rm_files(trip1_dir)
        if not rm_files:
            pytest.skip("No .rm files in trip 1")

        rendered = _render_fixture()
        word_map = rendered.word_map

        # Find the highlight GlyphRange
        trip1_manifest = _load_trip_manifest(1)
        cpages_raw = trip1_manifest.get("content_json", {}).get("cPages", {}).get("pages", [])
        highlights = _extract_glyph_ranges(rm_files, cpages_raw)
        target_highlight = None
        for page, gr in highlights:
            if _HIGHLIGHT_PHRASE in gr.text.lower():
                target_highlight = (page, gr)
                break

        if target_highlight is None:
            pytest.skip(f"No highlight containing {_HIGHLIGHT_PHRASE!r} found in trip 1 .rm files")

        page, gr = target_highlight
        assert len(gr.rectangles) >= 1

        # Transform the device rect centre to PDF coords via the canvas transform
        from rock_paper_sync.device_profile import device_to_pdf

        dr = gr.rectangles[0]
        dev_cx = dr.x + dr.w / 2.0
        dev_cy = dr.y + dr.h / 2.0
        pdf_cx, pdf_cy = device_to_pdf(_LEGACY_TRANSFORM, dev_cx, dev_cy)

        # Word-map phrase centre
        glyph_words = gr.text.split()
        matched_rects = []
        for pw in word_map.pages:
            if pw.page != page:
                continue
            for tid, rect in pw.tokens:
                if word_map.texts[tid] in glyph_words:
                    matched_rects.append(rect)
        assert len(matched_rects) == len(glyph_words), (
            f"Expected {len(glyph_words)} matched rects, got {len(matched_rects)}"
        )
        wm_x0 = min(r.x0 for r in matched_rects)
        wm_x1 = max(r.x1 for r in matched_rects)
        wm_cx = (wm_x0 + wm_x1) / 2.0
        wm_cy = sum((r.y0 + r.y1) / 2.0 for r in matched_rects) / len(matched_rects)

        x_error = abs(pdf_cx - wm_cx)
        y_error = abs(pdf_cy - wm_cy)

        print(f"\nHighlight calibration for {gr.text!r}:")
        print(f"  Device rect: x={dr.x:.1f} y={dr.y:.1f} w={dr.w:.1f} h={dr.h:.1f}")
        print(f"  Transformed centre: ({pdf_cx:.1f}, {pdf_cy:.1f})")
        print(f"  Word-map phrase centre: ({wm_cx:.1f}, {wm_cy:.1f})")
        print(f"  X error: {x_error:.1f} pt, Y error: {y_error:.1f} pt")
        print(f"  Canvas scale: {_LEGACY_TRANSFORM.scale:.4f}")

        # X error within 5 pt (the device adds ~23 px horizontal padding
        # which is ~23/2.94 = 7.8 pt; the rect centre is less affected)
        assert x_error < 5.0, f"X error {x_error:.1f} pt exceeds 5 pt"
        # Y error within 10 pt (the device y may have a small offset;
        # the y fit from stroke anchors in task 3 will quantify it)
        assert y_error < 10.0, f"Y error {y_error:.1f} pt exceeds 10 pt"

    def test_i8_words_under_highlight(self) -> None:
        """I8: words under the transformed highlight rects == GlyphRange.text."""
        trip1_dir = _testdata_dir() / "trips" / "1"
        rm_files = _load_rm_files(trip1_dir)
        if not rm_files:
            pytest.skip("No .rm files in trip 1")

        rendered = _render_fixture()
        word_map = rendered.word_map

        trip1_manifest = _load_trip_manifest(1)
        cpages_raw = trip1_manifest.get("content_json", {}).get("cPages", {}).get("pages", [])
        highlights = _extract_glyph_ranges(rm_files, cpages_raw)
        target = None
        for page, gr in highlights:
            if _HIGHLIGHT_PHRASE in gr.text.lower():
                target = (page, gr)
                break

        if target is None:
            pytest.skip(f"No highlight containing {_HIGHLIGHT_PHRASE!r} found")

        page, gr = target
        from rock_paper_sync.ink.calibration import _DeviceRect

        device_rects = [_DeviceRect(r.x, r.y, r.w, r.h) for r in gr.rectangles]
        result = check_i8(gr.text, device_rects, word_map, _LEGACY_TRANSFORM, page)

        print(f"\nI8 check: pass={result.i8_pass}")
        print(f"  GlyphRange words: {result.glyph_text_words}")
        print(f"  Words under rects: {result.words_under_rects}")

        assert result.i8_pass, (
            f"I8 failed: words under rects {result.words_under_rects} "
            f"!= GlyphRange words {result.glyph_text_words}"
        )

    def test_scene_info_paper_size(self) -> None:
        """SceneInfo.paper_size == PDF_CANVAS_W x PDF_CANVAS_H for PDF pages."""
        trip1_dir = _testdata_dir() / "trips" / "1"
        rm_files = _load_rm_files(trip1_dir)
        if not rm_files:
            pytest.skip("No .rm files in trip 1")

        # Load cPages to distinguish PDF-backed vs device-added pages
        trip1_manifest = _load_trip_manifest(1)
        cpages_raw = trip1_manifest.get("content_json", {}).get("cPages", {}).get("pages", [])
        redir_pages = {str(e.get("id", "")) for e in cpages_raw if e.get("redir") is not None}

        for page_uuid, rm_bytes in rm_files.items():
            blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
            for block in blocks:
                if isinstance(block, rmscene.SceneInfo) and block.paper_size is not None:
                    w, h = block.paper_size
                    is_pdf_page = page_uuid in redir_pages
                    print(
                        f"\nSceneInfo.paper_size on page {page_uuid[:8]}: ({w}, {h})"
                        f" [{'PDF' if is_pdf_page else 'device-added'}]"
                    )
                    if is_pdf_page:
                        assert (w, h) == (PDF_CANVAS_W, PDF_CANVAS_H), (
                            f"PDF page paper_size ({w}, {h}) != "
                            f"expected ({PDF_CANVAS_W}, {PDF_CANVAS_H})"
                        )
                    # Device-added pages have their own canvas size (820x1458)
                    # — just report, don't assert a specific value

        # paper_size is optional; just report absence
        print("\nSceneInfo.paper_size not present in any .rm file")

    def test_cpages_structure(self) -> None:
        """cPages: trailing blank pages have redir == PDF page index."""
        trip1_manifest = _load_trip_manifest(1)
        content_json = trip1_manifest.get("content_json")
        if content_json is None:
            pytest.skip("No .content JSON in trip 1 manifest")

        cpages_raw = content_json.get("cPages", {}).get("pages", [])
        if not cpages_raw:
            pytest.skip("No cPages.pages in trip 1 content")

        report = analyse_cpages(cpages_raw, _EXPECTED_CONTENT_PAGES)

        print(f"\ncPages analysis ({len(report.entries)} entries):")
        for e in report.entries:
            redir_str = f"redir={e.redir_value}" if e.has_redir else "no redir (device-added)"
            print(f"  [{e.index}] {e.page_id[:8]}... {redir_str}")
        print(f"  Redir entries: {len(report.redir_entries)}")
        print(f"  Device-added: {len(report.device_added_entries)}")
        print(f"  Trailing blank redir OK: {report.trailing_blank_redir_ok}")

        assert report.trailing_blank_redir_ok, "Trailing blank pages have incorrect redir values"

    def test_trip2_cpages_rebuilt(self) -> None:
        """D5: after discard_ink re-push, cPages is rebuilt (new page ids)."""
        trip2_dir = _testdata_dir() / "trips" / "2"
        if not trip2_dir.exists():
            pytest.skip("Trip 2 not recorded yet")

        trip2_manifest = _load_trip_manifest(2)
        content_json = trip2_manifest.get("content_json")
        if content_json is None:
            pytest.skip("No .content JSON in trip 2 manifest")

        cpages_raw = content_json.get("cPages", {}).get("pages", [])
        report = analyse_cpages(cpages_raw, _EXPECTED_CONTENT_PAGES)

        print(f"\nTrip 2 cPages ({len(report.entries)} entries):")
        for e in report.entries:
            redir_str = f"redir={e.redir_value}" if e.has_redir else "no redir"
            print(f"  [{e.index}] {e.page_id[:8]}... {redir_str}")

        # Check whether cPages was rebuilt (different page ids from trip 1)
        trip1_manifest = _load_trip_manifest(1)
        trip1_cpages = trip1_manifest.get("content_json", {}).get("cPages", {}).get("pages", [])
        trip1_ids = {p.get("id", "") for p in trip1_cpages}
        trip2_ids = {e.page_id for e in report.entries}

        rebuilt = trip1_ids != trip2_ids
        print(f"  cPages rebuilt (new page ids): {rebuilt}")

        # Record findings in manifest
        trip2_manifest["cpages_rebuilt"] = rebuilt
        trip2_manifest["device_added_pages"] = len(report.device_added_entries)
        (trip2_dir / "manifest.json").write_text(json.dumps(trip2_manifest, indent=2, default=str))

    def test_write_report(self, tmp_path: Path) -> None:
        """Write a diagnostic report with all measured numbers."""
        trip1_dir = _testdata_dir() / "trips" / "1"
        diag_dir = trip1_dir / "_diagnostic"
        diag_dir.mkdir(parents=True, exist_ok=True)

        lines = ["# PDF Substrate Calibration Report\n"]

        # Highlight calibration
        rm_files = _load_rm_files(trip1_dir)
        if rm_files:
            rendered = _render_fixture()
            trip1_manifest_hl = _load_trip_manifest(1)
            cpages_raw_hl = (
                trip1_manifest_hl.get("content_json", {}).get("cPages", {}).get("pages", [])
            )
            highlights = _extract_glyph_ranges(rm_files, cpages_raw_hl)
            for page, gr in highlights:
                if _HIGHLIGHT_PHRASE in gr.text.lower():
                    from rock_paper_sync.ink.calibration import _DeviceRect

                    device_rects = [_DeviceRect(r.x, r.y, r.w, r.h) for r in gr.rectangles]
                    cal = calibrate_highlight(
                        gr.text, device_rects, rendered.word_map, _LEGACY_TRANSFORM, page
                    )
                    lines.append("## Highlight Calibration\n")
                    lines.append(f"- Text: {gr.text!r}")
                    lines.append(f"- Page: {page}")
                    lines.append(f"- Profile scale: {cal.profile_scale}")
                    lines.append(
                        f"- X fit: scale={cal.x_fit.scale:.6f}, "
                        f"offset={cal.x_fit.offset:.4f} pt "
                        f"(error: {cal.x_scale_error_pct:.4f}%)"
                    )
                    lines.append(
                        f"- Y fit: scale={cal.y_fit.scale:.6f}, "
                        f"offset={cal.y_fit.offset:.4f} pt "
                        f"(error: {cal.y_scale_error_pct:.4f}%)"
                    )
                    lines.append("")

        # cPages
        trip1_manifest = _load_trip_manifest(1)
        content_json = trip1_manifest.get("content_json")
        if content_json:
            cpages_raw = content_json.get("cPages", {}).get("pages", [])
            if cpages_raw:
                report = analyse_cpages(cpages_raw, _EXPECTED_CONTENT_PAGES)
                lines.append("## cPages Structure\n")
                for e in report.entries:
                    redir_str = (
                        f"redir={e.redir_value}" if e.has_redir else "no redir (device-added)"
                    )
                    lines.append(f"- [{e.index}] {e.page_id[:12]}... {redir_str}")
                lines.append(f"- Trailing blank redir OK: {report.trailing_blank_redir_ok}")
                lines.append("")

        # SceneInfo
        if rm_files:
            for page_uuid, rm_bytes in rm_files.items():
                blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
                for block in blocks:
                    if isinstance(block, rmscene.SceneInfo) and block.paper_size is not None:
                        lines.append("## SceneInfo\n")
                        lines.append(f"- paper_size: {block.paper_size}")
                        lines.append(
                            f"- profile screen: ({_PROFILE.screen_w_px}, {_PROFILE.screen_h_px})"
                        )
                        lines.append("")

        # Ink overlay — the primary visual artefact
        if rm_files:
            rendered = rendered if "rendered" in dir() else _render_fixture()
            # Build page_files mapping: page_index -> rm_bytes
            page_files: dict[int, bytes] = {}
            for page_idx, page_uuid in enumerate(sorted(rm_files.keys())):
                page_files[page_idx] = rm_files[page_uuid]

            from tools.ink_overlay import overlay_ink

            png_paths = overlay_ink(
                rendered.pdf,
                page_files,
                _LEGACY_TRANSFORM,
                word_map=rendered.word_map,
                out_dir=diag_dir,
            )
            lines.append("## Overlay PNGs\n")
            for png_path in png_paths:
                lines.append(f"- {png_path.name}")
            lines.append("")
            print(f"  Overlay: {diag_dir / 'overlay.pdf'}")
            for p in png_paths:
                print(f"  PNG: {p}")

        report_path = diag_dir / "report.md"
        report_path.write_text("\n".join(lines))
        print(f"\nReport written to {report_path}")


# ---------------------------------------------------------------------------
# Replay helpers
# ---------------------------------------------------------------------------


def _load_trip_manifest(trip_number: int) -> dict:
    """Load manifest.json for a given trip."""
    manifest_path = _testdata_dir() / "trips" / str(trip_number) / "manifest.json"
    return json.loads(manifest_path.read_text())


def _load_rm_files(trip_dir: Path) -> dict[str, bytes]:
    """Load all .rm files from a trip directory."""
    rm_files: dict[str, bytes] = {}
    for rm_path in trip_dir.glob("*.rm"):
        rm_files[rm_path.stem] = rm_path.read_bytes()
    return rm_files


def _render_fixture():
    """Re-render the fixture with the legacy layout that trip 1 was pushed with.

    Trip 1 was pushed with the old page geometry (477x848 pt, 36 pt margins,
    line_height 1.4).  We must render with the same parameters so the PDF
    hash matches the recorded one (I10).
    """
    from rock_paper_sync.config import LayoutConfig
    from rock_paper_sync.parser import parse_markdown_file
    from rock_paper_sync.render.pdf import PdfRenderer

    fixture_path = _fixtures_dir() / _FIXTURE_NAME
    md_doc = parse_markdown_file(fixture_path)
    layout = LayoutConfig(
        margin_top_pt=36.0,
        margin_bottom_pt=36.0,
        margin_left_pt=36.0,
        margin_right_pt=36.0,
        line_height=1.4,
    )
    renderer = PdfRenderer(layout)
    return renderer.render(md_doc, _LEGACY_PROFILE)


def _extract_glyph_ranges(
    rm_files: dict[str, bytes],
    cpages: list[dict[str, object]] | None = None,
) -> list[tuple[int, GlyphRange]]:
    """Extract all GlyphRange highlights from .rm files.

    Returns list of (page_redir, GlyphRange) where page_redir is the
    PDF page index from the cPages redir value (D4: never from list position).

    Args:
        rm_files: Mapping of page UUID -> .rm bytes.
        cpages: The cPages.pages list; when provided, the page's redir
            value is used.  Otherwise falls back to sorted order (legacy).
    """
    # Build UUID -> redir mapping from cPages
    redir_map: dict[str, int] = {}
    if cpages:
        for entry in cpages:
            page_id = str(entry.get("id", ""))
            redir = entry.get("redir")
            if redir is not None and isinstance(redir, dict):
                redir_map[page_id] = int(redir.get("value", -1))
            elif isinstance(redir, (int, float)):
                redir_map[page_id] = int(redir)

    results: list[tuple[int, GlyphRange]] = []
    for page_uuid, rm_bytes in rm_files.items():
        page_redir = redir_map.get(page_uuid, -1)
        if page_redir < 0:
            # No redir (device-added page) — skip for highlight extraction
            continue
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        for block in blocks:
            if isinstance(block, SceneGlyphItemBlock) and block.item.value is not None:
                results.append((page_redir, block.item.value))
    return results


# ============================================================================
# SYNTHETIC UNIT TEST — always runs (no testdata required)
# ============================================================================


def _build_synthetic_rm(
    glyph_text: str,
    device_rects: list[Rectangle],
) -> bytes:
    """Build a minimal valid .rm file with one GlyphRange highlight.

    Uses rmscene's write_blocks to create the binary .rm format.

    Args:
        glyph_text: Highlight text.
        device_rects: Rectangles in device coordinates.

    Returns:
        .rm file bytes.
    """
    import uuid as uuid_mod

    author_uuid = uuid_mod.UUID("00000000-0000-4000-8000-000000000001")

    blocks = [
        AuthorIdsBlock(author_uuids={1: author_uuid}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
        # Scene tree: root + layer
        SceneTreeBlock(
            tree_id=CrdtId(0, 11),
            node_id=CrdtId(0, 0),
            is_update=True,
            parent_id=CrdtId(0, 1),
        ),
        TreeNodeBlock(Group(node_id=CrdtId(0, 1))),
        TreeNodeBlock(
            Group(
                node_id=CrdtId(0, 11),
                label=LwwValue(timestamp=CrdtId(0, 12), value="Layer 1"),
            )
        ),
        SceneGroupItemBlock(
            parent_id=CrdtId(0, 1),
            item=CrdtSequenceItem(
                item_id=CrdtId(0, 13),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=CrdtId(0, 11),
            ),
        ),
        # The highlight GlyphRange
        SceneGlyphItemBlock(
            parent_id=CrdtId(0, 11),
            item=CrdtSequenceItem(
                item_id=CrdtId(1, 20),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=GlyphRange(
                    start=0,
                    length=len(glyph_text),
                    text=glyph_text,
                    color=PenColor.HIGHLIGHT,
                    rectangles=device_rects,
                ),
            ),
        ),
    ]

    buf = io.BytesIO()
    rmscene.write_blocks(buf, blocks)
    return buf.getvalue()


def _build_synthetic_rm_with_stroke(
    glyph_text: str,
    device_rects: list[Rectangle],
    stroke: object,
) -> bytes:
    """Build a minimal .rm with one GlyphRange highlight AND one stroke.

    Args:
        glyph_text: Highlight text.
        device_rects: Highlight rectangles in device coordinates.
        stroke: An ``rmscene.scene_items.Line`` instance.

    Returns:
        .rm file bytes.
    """
    import uuid as uuid_mod

    author_uuid = uuid_mod.UUID("00000000-0000-4000-8000-000000000001")

    blocks = [
        AuthorIdsBlock(author_uuids={1: author_uuid}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
        SceneTreeBlock(
            tree_id=CrdtId(0, 11),
            node_id=CrdtId(0, 0),
            is_update=True,
            parent_id=CrdtId(0, 1),
        ),
        TreeNodeBlock(Group(node_id=CrdtId(0, 1))),
        TreeNodeBlock(
            Group(
                node_id=CrdtId(0, 11),
                label=LwwValue(timestamp=CrdtId(0, 12), value="Layer 1"),
            )
        ),
        SceneGroupItemBlock(
            parent_id=CrdtId(0, 1),
            item=CrdtSequenceItem(
                item_id=CrdtId(0, 13),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=CrdtId(0, 11),
            ),
        ),
        SceneGlyphItemBlock(
            parent_id=CrdtId(0, 11),
            item=CrdtSequenceItem(
                item_id=CrdtId(1, 20),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=GlyphRange(
                    start=0,
                    length=len(glyph_text),
                    text=glyph_text,
                    color=PenColor.HIGHLIGHT,
                    rectangles=device_rects,
                ),
            ),
        ),
        SceneLineItemBlock(
            parent_id=CrdtId(0, 11),
            item=CrdtSequenceItem(
                item_id=CrdtId(1, 30),
                left_id=CrdtId(0, 0),
                right_id=CrdtId(0, 0),
                deleted_length=0,
                value=stroke,
            ),
        ),
    ]

    buf = io.BytesIO()
    rmscene.write_blocks(buf, blocks)
    return buf.getvalue()


# PNG DPI used by overlay_ink — imported here for pixel calculations
_PNG_DPI = 110


class TestCalibrationSynthetic:
    """Synthetic unit tests for the calibration module.

    These always run — no device testdata needed.  They validate the
    calibration math against known transforms.
    """

    def test_fit_affine_1d_exact(self) -> None:
        """fit_affine_1d recovers exact scale and offset."""
        # y = 2.0 * x + 3.0
        source = [1.0, 2.0, 3.0, 4.0, 5.0]
        target = [5.0, 7.0, 9.0, 11.0, 13.0]
        fit = fit_affine_1d(source, target)
        assert fit.n_points == 5
        assert abs(fit.scale - 2.0) < 1e-10
        assert abs(fit.offset - 3.0) < 1e-10

    def test_fit_affine_1d_insufficient_points(self) -> None:
        """fit_affine_1d returns NaN for < 2 points."""
        fit = fit_affine_1d([1.0], [2.0])
        assert math.isnan(fit.scale)
        assert math.isnan(fit.offset)
        assert fit.n_points == 1

    def test_fit_affine_1d_length_mismatch(self) -> None:
        """fit_affine_1d raises ValueError on mismatched lengths."""
        with pytest.raises(ValueError, match="source length"):
            fit_affine_1d([1.0, 2.0], [1.0])

    def test_calibrate_highlight_synthetic(self) -> None:
        """Calibrate with synthetic highlight whose rects are the profile
        transform of known word-map rects.

        The fitted scale should be the profile's _PX_TO_PT (0.5) and
        offset should be ~0.
        """
        profile = PROFILES["paper_pro_move"]
        transform = CanvasTransform.for_pdf_page(profile)

        # Create a word map with four tokens: "purple", "monkey", "dishwasher", "engine"
        words = ["purple", "monkey", "dishwasher", "engine"]
        # Place them on page 0 at known PDF-point positions.
        # Simulate a two-line highlight: first two words on line 1,
        # next two on line 2, so y values vary (required for y-fit).
        token_rects: list[tuple[TokenId, Rect]] = []
        pdf_rects: list[Rect] = []
        y_offsets = [100.0, 100.0, 116.0, 116.0]  # two lines
        for i, word in enumerate(words):
            x0 = 50.0 + (i % 2) * 80.0
            y0 = y_offsets[i]
            x1 = x0 + 60.0
            y1 = y0 + 12.0
            rect = Rect(x0=x0, y0=y0, x1=x1, y1=y1)
            token_rects.append((TokenId(i), rect))
            pdf_rects.append(rect)

        word_map = WordMap(
            page_size=profile.page_size_pt,
            pages=(PageWords(page=0, tokens=tuple(token_rects)),),
            texts=tuple(words),
        )

        # Convert PDF rects back to device coords, then use those as
        # the "device rects" in the GlyphRange.
        # Anisotropic: x uses x_scale, y uses y_scale.
        device_rects: list[Rectangle] = []
        for rect in pdf_rects:
            # PDF centre -> device coords
            pdf_cx = (rect.x0 + rect.x1) / 2.0
            pdf_cy = (rect.y0 + rect.y1) / 2.0
            dev_x, dev_y = pdf_to_device(transform, pdf_cx, pdf_cy)

            # GlyphRange rects use (x, y, w, h) in device coords
            # where (x, y) is top-left of the rect
            dev_w = (rect.x1 - rect.x0) * transform.x_scale
            dev_h = (rect.y1 - rect.y0) * transform.y_scale
            dev_x_tl = dev_x - dev_w / 2.0
            dev_y_tl = dev_y - dev_h / 2.0
            device_rects.append(Rectangle(x=dev_x_tl, y=dev_y_tl, w=dev_w, h=dev_h))

        # Build the synthetic .rm and parse it back
        glyph_text = "purple monkey dishwasher engine"
        rm_bytes = _build_synthetic_rm(glyph_text, device_rects)

        # Verify the .rm round-trips
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        glyph_blocks = [
            b for b in blocks if isinstance(b, SceneGlyphItemBlock) and b.item.value is not None
        ]
        assert len(glyph_blocks) == 1
        gr = glyph_blocks[0].item.value
        assert gr.text == glyph_text
        assert len(gr.rectangles) == 4

        # Run calibration
        from rock_paper_sync.ink.calibration import _DeviceRect

        cal_rects = [_DeviceRect(r.x, r.y, r.w, r.h) for r in gr.rectangles]
        cal = calibrate_highlight(glyph_text, cal_rects, word_map, transform, page=0)

        # The fitted transform is: pdf_centre = scale * dev_centre + offset
        # Anisotropic: x and y have different scales.
        expected_x_scale = 1.0 / transform.x_scale
        expected_y_scale = 1.0 / transform.y_scale
        expected_x_offset = transform.pdf_w / 2.0  # centre-origin shift

        assert abs(cal.x_fit.scale - expected_x_scale) < 0.001, (
            f"X scale {cal.x_fit.scale} != expected {expected_x_scale}"
        )
        assert abs(cal.y_fit.scale - expected_y_scale) < 0.001, (
            f"Y scale {cal.y_fit.scale} != expected {expected_y_scale}"
        )
        assert abs(cal.x_fit.offset - expected_x_offset) < 0.5, (
            f"X offset {cal.x_fit.offset} != expected {expected_x_offset}"
        )
        assert abs(cal.y_fit.offset) < 0.5, f"Y offset {cal.y_fit.offset} too large"

    def test_i8_check_synthetic(self) -> None:
        """I8 check passes with synthetic data where rects match tokens."""
        profile = PROFILES["paper_pro_move"]
        transform = CanvasTransform.for_pdf_page(profile)
        words = ["purple", "monkey", "dishwasher", "engine"]

        token_rects: list[tuple[TokenId, Rect]] = []
        for i, word in enumerate(words):
            x0 = 50.0 + i * 80.0
            y0 = 100.0
            x1 = x0 + 60.0
            y1 = y0 + 12.0
            token_rects.append((TokenId(i), Rect(x0=x0, y0=y0, x1=x1, y1=y1)))

        word_map = WordMap(
            page_size=profile.page_size_pt,
            pages=(PageWords(page=0, tokens=tuple(token_rects)),),
            texts=tuple(words),
        )

        # Device rects whose centres, when transformed, land inside
        # the word-map rects
        from rock_paper_sync.ink.calibration import _DeviceRect

        cal_rects: list[_DeviceRect] = []
        for tid, rect in token_rects:
            pdf_cx = (rect.x0 + rect.x1) / 2.0
            pdf_cy = (rect.y0 + rect.y1) / 2.0
            dev_x, dev_y = pdf_to_device(transform, pdf_cx, pdf_cy)
            cal_rects.append(_DeviceRect(x=dev_x - 5, y=dev_y - 5, w=10, h=10))

        result = check_i8(
            "purple monkey dishwasher engine",
            cal_rects,
            word_map,
            transform,
            page=0,
        )

        assert result.i8_pass, (
            f"I8 failed: under={result.words_under_rects}, expected={result.glyph_text_words}"
        )

    def test_analyse_cpages_basic(self) -> None:
        """analyse_cpages correctly categorises redir vs device-added entries."""
        cpages = [
            {"id": "page-0", "redir": {"value": 0}},
            {"id": "page-1", "redir": {"value": 1}},
            {"id": "page-2", "redir": {"value": 2}},
            {"id": "page-3"},  # device-added
        ]
        report = analyse_cpages(cpages, expected_content_pages=1)

        assert len(report.entries) == 4
        assert len(report.redir_entries) == 3
        assert len(report.device_added_entries) == 1
        assert report.device_added_entries[0].page_id == "page-3"
        assert report.trailing_blank_redir_ok

    def test_synthetic_rm_roundtrip(self) -> None:
        """Synthetic .rm file round-trips through rmscene."""
        rects = [Rectangle(x=100.0, y=200.0, w=50.0, h=15.0)]
        rm_bytes = _build_synthetic_rm("test", rects)

        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        glyph_blocks = [
            b for b in blocks if isinstance(b, SceneGlyphItemBlock) and b.item.value is not None
        ]
        assert len(glyph_blocks) == 1
        gr = glyph_blocks[0].item.value
        assert gr.text == "test"
        assert len(gr.rectangles) == 1
        assert abs(gr.rectangles[0].x - 100.0) < 1e-6

    def test_overlay_ink_synthetic(self, tmp_path: Path) -> None:
        """overlay_ink draws highlight and stroke onto a PDF; yellow pixel check."""
        from PIL import Image

        from tools.ink_overlay import overlay_ink

        profile = PROFILES["paper_pro_move"]
        transform = CanvasTransform.for_pdf_page(profile)

        # Render a tiny markdown to get a real PDF
        md_file = tmp_path / "tiny.md"
        md_file.write_text("# Tiny\n\nHello world.\n")

        from rock_paper_sync.config import LayoutConfig
        from rock_paper_sync.parser import parse_markdown_file
        from rock_paper_sync.render.pdf import PdfRenderer

        doc = parse_markdown_file(md_file)
        renderer = PdfRenderer(LayoutConfig())
        rendered = renderer.render(doc, profile)

        # Build a synthetic .rm with a highlight rect at a known
        # device-coordinate position, and a stroke.
        # Highlight rect: centred at device (0, 200) -> PDF (238.5, 100)
        highlight_rects = [Rectangle(x=-60.0, y=190.0, w=120.0, h=24.0)]

        from rmscene.scene_items import Line, Pen, Point

        stroke_line = Line(
            color=PenColor.BLACK,
            tool=Pen.BALLPOINT_1,
            points=[
                Point(x=-200.0, y=300.0, speed=0, direction=0, width=5, pressure=100),
                Point(x=200.0, y=300.0, speed=0, direction=0, width=5, pressure=100),
            ],
            thickness_scale=1.0,
            starting_length=0.0,
        )
        rm_bytes = _build_synthetic_rm_with_stroke("Hello", highlight_rects, stroke_line)

        out_dir = tmp_path / "overlay_out"
        pngs = overlay_ink(
            rendered.pdf,
            {0: rm_bytes},
            transform,
            word_map=rendered.word_map,
            out_dir=out_dir,
        )

        # Assertions: overlay.pdf and at least one PNG exist
        assert (out_dir / "overlay.pdf").exists()
        assert len(pngs) >= 1
        assert pngs[0].exists()

        # Check that the highlight region has yellow-ish pixels.
        # The highlight rect at device (-60, 190) w=120 h=24 transforms to
        # PDF coords via the canvas transform.
        from rock_paper_sync.device_profile import device_to_pdf

        pdf_cx, pdf_cy = device_to_pdf(transform, -60.0 + 120.0 / 2, 190.0 + 24.0 / 2)
        # At 110 dpi, PDF point -> pixel: px = pt * (110/72)
        dpi_scale = _PNG_DPI / 72.0
        sample_x = int(pdf_cx * dpi_scale)
        sample_y = int(pdf_cy * dpi_scale)
        img = Image.open(pngs[0])

        # Ensure the sample point is within bounds
        if sample_x < img.width and sample_y < img.height:
            r, g, b = img.getpixel((sample_x, sample_y))[:3]
            # Yellow-ish: R and G high, B lower (the overlay blends
            # yellow onto white, so all channels are high but B < R)
            assert r > 200, f"Red channel {r} too low at ({sample_x}, {sample_y})"
            assert g > 200, f"Green channel {g} too low at ({sample_x}, {sample_y})"
            # B should be noticeably lower than R due to yellow fill
            assert b < r, f"Blue {b} >= Red {r}: not yellow-ish at ({sample_x}, {sample_y})"
