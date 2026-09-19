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
    SceneTreeBlock,
    TreeNodeBlock,
)
from rmscene.scene_items import GlyphRange, Group, PenColor, Rectangle

from rock_paper_sync.device_profile import PROFILES, pdf_to_device
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

# Expected page count: 1 content + 2 trailing blank = 3 total
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
    engine = SyncEngine(config)
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
    if not td.exists() or not (td / "trips" / "1").exists():
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
        """Measured transform: scale within 1%, offset within 2 pt."""
        trip1_dir = _testdata_dir() / "trips" / "1"
        rm_files = _load_rm_files(trip1_dir)
        if not rm_files:
            pytest.skip("No .rm files in trip 1")

        rendered = _render_fixture()
        word_map = rendered.word_map

        # Find the highlight GlyphRange
        highlights = _extract_glyph_ranges(rm_files)
        target_highlight = None
        for page, gr in highlights:
            if _HIGHLIGHT_PHRASE in gr.text.lower():
                target_highlight = (page, gr)
                break

        if target_highlight is None:
            pytest.skip(f"No highlight containing {_HIGHLIGHT_PHRASE!r} found in trip 1 .rm files")

        page, gr = target_highlight
        from rock_paper_sync.ink.calibration import _DeviceRect

        device_rects = [_DeviceRect(r.x, r.y, r.w, r.h) for r in gr.rectangles]

        cal = calibrate_highlight(gr.text, device_rects, word_map, _PROFILE, page)

        print(f"\nHighlight calibration for {gr.text!r}:")
        print(f"  X fit: scale={cal.x_fit.scale:.6f}, offset={cal.x_fit.offset:.4f} pt")
        print(f"  Y fit: scale={cal.y_fit.scale:.6f}, offset={cal.y_fit.offset:.4f} pt")
        print(f"  Profile scale: {cal.profile_scale}")
        print(f"  X scale error: {cal.x_scale_error_pct:.4f}%")
        print(f"  Y scale error: {cal.y_scale_error_pct:.4f}%")
        print(f"  X offset error: {cal.x_offset_error_pt:.4f} pt")
        print(f"  Y offset error: {cal.y_offset_error_pt:.4f} pt")

        assert cal.x_scale_error_pct < 1.0, f"X scale error {cal.x_scale_error_pct:.4f}% exceeds 1%"
        assert cal.y_scale_error_pct < 1.0, f"Y scale error {cal.y_scale_error_pct:.4f}% exceeds 1%"
        assert cal.x_offset_error_pt < 2.0, f"X offset {cal.x_offset_error_pt:.4f} pt exceeds 2 pt"
        assert cal.y_offset_error_pt < 2.0, f"Y offset {cal.y_offset_error_pt:.4f} pt exceeds 2 pt"

    def test_i8_words_under_highlight(self) -> None:
        """I8: words under the transformed highlight rects == GlyphRange.text."""
        trip1_dir = _testdata_dir() / "trips" / "1"
        rm_files = _load_rm_files(trip1_dir)
        if not rm_files:
            pytest.skip("No .rm files in trip 1")

        rendered = _render_fixture()
        word_map = rendered.word_map

        highlights = _extract_glyph_ranges(rm_files)
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
        result = check_i8(gr.text, device_rects, word_map, _PROFILE, page)

        print(f"\nI8 check: pass={result.i8_pass}")
        print(f"  GlyphRange words: {result.glyph_text_words}")
        print(f"  Words under rects: {result.words_under_rects}")

        assert result.i8_pass, (
            f"I8 failed: words under rects {result.words_under_rects} "
            f"!= GlyphRange words {result.glyph_text_words}"
        )

    def test_scene_info_paper_size(self) -> None:
        """SceneInfo.paper_size vs profile screen size (if present in rmscene >= 0.7)."""
        trip1_dir = _testdata_dir() / "trips" / "1"
        rm_files = _load_rm_files(trip1_dir)
        if not rm_files:
            pytest.skip("No .rm files in trip 1")

        for page_uuid, rm_bytes in rm_files.items():
            blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
            for block in blocks:
                if isinstance(block, rmscene.SceneInfo):
                    if block.paper_size is not None:
                        w, h = block.paper_size
                        print(f"\nSceneInfo.paper_size on page {page_uuid}: ({w}, {h})")
                        print(
                            f"Profile screen size: ({_PROFILE.screen_w_px}, {_PROFILE.screen_h_px})"
                        )
                        # Report; assert equality if present
                        assert (w, h) == (
                            _PROFILE.screen_w_px,
                            _PROFILE.screen_h_px,
                        ), (
                            f"SceneInfo.paper_size ({w}, {h}) != "
                            f"profile ({_PROFILE.screen_w_px}, {_PROFILE.screen_h_px})"
                        )
                        return

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
            highlights = _extract_glyph_ranges(rm_files)
            for page, gr in highlights:
                if _HIGHLIGHT_PHRASE in gr.text.lower():
                    from rock_paper_sync.ink.calibration import _DeviceRect

                    device_rects = [_DeviceRect(r.x, r.y, r.w, r.h) for r in gr.rectangles]
                    cal = calibrate_highlight(
                        gr.text, device_rects, rendered.word_map, _PROFILE, page
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
    """Re-render the fixture and return the RenderedDocument."""
    from rock_paper_sync.config import LayoutConfig
    from rock_paper_sync.parser import parse_markdown_file
    from rock_paper_sync.render.pdf import PdfRenderer

    fixture_path = _fixtures_dir() / _FIXTURE_NAME
    md_doc = parse_markdown_file(fixture_path)
    layout = LayoutConfig()
    renderer = PdfRenderer(layout)
    return renderer.render(md_doc, _PROFILE)


def _extract_glyph_ranges(rm_files: dict[str, bytes]) -> list[tuple[int, GlyphRange]]:
    """Extract all GlyphRange highlights from .rm files.

    Returns list of (page_index, GlyphRange).  Page index is inferred
    from the file ordering (sorted by key).
    """
    results: list[tuple[int, GlyphRange]] = []
    for page_idx, page_uuid in enumerate(sorted(rm_files.keys())):
        rm_bytes = rm_files[page_uuid]
        blocks = list(rmscene.read_blocks(io.BytesIO(rm_bytes)))
        for block in blocks:
            if isinstance(block, SceneGlyphItemBlock) and block.item.value is not None:
                results.append((page_idx, block.item.value))
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
        # device_to_pdf: pdf_x = (dev_x + w/2) * scale, pdf_y = dev_y * scale
        # pdf_to_device inverts that.
        scale = profile.page_width_pt / profile.screen_w_px  # 0.5
        device_rects: list[Rectangle] = []
        for rect in pdf_rects:
            # PDF centre -> device coords
            pdf_cx = (rect.x0 + rect.x1) / 2.0
            pdf_cy = (rect.y0 + rect.y1) / 2.0
            dev_x, dev_y = pdf_to_device(profile, pdf_cx, pdf_cy)

            # GlyphRange rects use (x, y, w, h) in device coords
            # where (x, y) is top-left of the rect
            dev_w = (rect.x1 - rect.x0) / scale
            dev_h = (rect.y1 - rect.y0) / scale
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
        cal = calibrate_highlight(glyph_text, cal_rects, word_map, profile, page=0)

        # The fitted transform is: pdf_centre = scale * dev_centre + offset
        # For x: pdf_x = (dev_x + screen_w/2) * scale = scale*dev_x + screen_w/2*scale
        #   => fitted scale == 0.5, fitted offset == screen_w/2 * scale = 238.5
        # For y: pdf_y = dev_y * scale
        #   => fitted scale == 0.5, fitted offset == 0
        expected_scale = scale
        expected_x_offset = (profile.screen_w_px / 2.0) * scale

        assert abs(cal.x_fit.scale - expected_scale) < 0.001, (
            f"X scale {cal.x_fit.scale} != expected {expected_scale}"
        )
        assert abs(cal.y_fit.scale - expected_scale) < 0.001, (
            f"Y scale {cal.y_fit.scale} != expected {expected_scale}"
        )
        assert abs(cal.x_fit.offset - expected_x_offset) < 0.5, (
            f"X offset {cal.x_fit.offset} != expected {expected_x_offset}"
        )
        assert abs(cal.y_fit.offset) < 0.5, f"Y offset {cal.y_fit.offset} too large"

    def test_i8_check_synthetic(self) -> None:
        """I8 check passes with synthetic data where rects match tokens."""
        profile = PROFILES["paper_pro_move"]
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
            dev_x, dev_y = pdf_to_device(profile, pdf_cx, pdf_cy)
            cal_rects.append(_DeviceRect(x=dev_x - 5, y=dev_y - 5, w=10, h=10))

        result = check_i8(
            "purple monkey dishwasher engine",
            cal_rects,
            word_map,
            profile,
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
