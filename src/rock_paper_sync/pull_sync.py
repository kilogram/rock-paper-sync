"""Pull sync engine for reMarkable -> Obsidian (Phase 4, PDF substrate).

Extracts ink from device .rm files, classifies it against the pushed
``WordMap``, and applies edits to the vault markdown file.

Pull pipeline:
    1. Load ``sync_state`` (word_map, pushed_pdf_sha256).
    2. Read the document index through ``CloudReader``; verify the
       ``.pdf`` entry hash == ``pushed_pdf_sha256`` (I10).
    3. Download ``.content`` -> ``analyse_cpages``; download page ``.rm``
       files; run I8 on every ``GlyphRange``.
    4. candidates -> clusters -> classify -> OCR for ``appended`` clusters
       -> edits -> apply.
    5. Write order (I12): (a) attachments (idempotent by name),
       (b) atomic replace of the markdown, (c) ONE SQLite transaction.

Invariants relied upon:
    INV-I3:  Pull cannot write to the cloud (CloudReader only;
             import-linter forbids sync_v3, rm_cloud_sync).
    INV-I4:  A consumed stroke never re-enters a candidate set.
    INV-I10: Ink is interpreted only against the PDF on the device.
    INV-I12: Pull is crash-safe toward duplicates, not loss.

Import restrictions (enforced by import-linter):
    Must NOT import ``rock_paper_sync.sync_v3`` or
    ``rock_paper_sync.rm_cloud_sync``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid as uuid_module
from dataclasses import dataclass
from pathlib import Path

from rock_paper_sync.cloud_reader import CloudReader
from rock_paper_sync.config import InkConfig, LayoutConfig, VaultConfig
from rock_paper_sync.device_profile import CanvasTransform, device_to_pdf
from rock_paper_sync.errors import RenderMismatchError, TransformMismatchError
from rock_paper_sync.ink.apply import apply_edits, build_token_spans, write_markdown_atomic
from rock_paper_sync.ink.bake import (
    bake_cluster,
    get_attachment_folder,
    render_cluster_to_png,
)
from rock_paper_sync.ink.calibration import DeviceRect, check_i8
from rock_paper_sync.ink.candidates import CandidateStroke, extract_candidates
from rock_paper_sync.ink.classify import (
    Classification,
    GlyphHighlight,
    classify_cluster,
    classify_glyph_highlights,
)
from rock_paper_sync.ink.clusters import InkCluster, cluster_candidates
from rock_paper_sync.ink.dispositions import (
    DOCUMENT_END,
    Baked,
    Consumed,
    Deferred,
    Disposition,
    Highlight,
    InkAnchor,
    MarkdownEdit,
    is_terminal,
)
from rock_paper_sync.ink.gestures import GestureResult, classify_gesture
from rock_paper_sync.render.wordmap import Rect, WordMap
from rock_paper_sync.state import PullState, StateManager

logger = logging.getLogger("rock_paper_sync.pull_sync")


@dataclass
class PullResult:
    """Result of pulling annotations for a single file."""

    vault_name: str
    obsidian_path: str
    success: bool
    highlights_added: int = 0
    notes_appended: int = 0
    drawings_saved: int = 0
    deferred_count: int = 0
    highlight_fallback_count: int = 0  # highlights that fell back to substring matching
    deferred_reasons: list[str] | None = None
    error: str | None = None
    ticks: int = 0
    strikethroughs: int = 0
    insertions: int = 0
    corrections: int = 0
    gestures_uninterpreted: int = 0


@dataclass
class PullStats:
    """Statistics for a pull sync operation."""

    files_checked: int = 0
    files_updated: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    total_highlights: int = 0
    total_notes: int = 0
    total_drawings: int = 0
    total_deferred: int = 0


class PullSyncEngine:
    """Orchestrates pull sync from reMarkable to Obsidian (Phase 4).

    Takes ``CloudReader`` (read-only) + ``StateManager`` + renderer-free
    dependencies. Never writes to the cloud (I3).
    """

    def __init__(
        self,
        state: StateManager,
        cloud_reader: CloudReader,
        layout: LayoutConfig,
        cache_dir: Path,
        ocr_processor: object | None = None,
        ink_config: InkConfig | None = None,
        ocr_confidence_threshold: float = 0.7,
    ) -> None:
        """Initialise pull sync engine.

        Args:
            state: State manager for tracking sync status.
            cloud_reader: Read-only cloud interface.
            layout: Layout configuration (for notes_heading, profile).
            cache_dir: Directory for caching downloaded files.
            ocr_processor: Optional OCR processor (Phase 4: simple interface).
            ink_config: Ink gesture configuration (default: ``InkConfig()``).
            ocr_confidence_threshold: Minimum OCR confidence to accept a
                gesture's text; below this the gesture is ``Baked`` with
                an ``UNINTERPRETED`` marker.  Passed from
                ``OCRConfig.confidence_threshold``.
        """
        self.state = state
        self.cloud_reader = cloud_reader
        self.layout = layout
        self.cache_dir = cache_dir
        self.ocr_processor = ocr_processor
        self.ink_config = ink_config or InkConfig()
        self.ocr_confidence_threshold = ocr_confidence_threshold
        self._profile = layout.get_device_profile()
        self._transform = CanvasTransform.for_pdf_page(self._profile)

    def pull_file(
        self,
        vault: VaultConfig,
        obsidian_path: str,
    ) -> PullResult:
        """Pull ink from device for a single file.

        Args:
            vault: Vault configuration.
            obsidian_path: Relative path within the vault.

        Returns:
            ``PullResult`` with statistics and status.
        """
        try:
            return self._pull_file_inner(vault, obsidian_path)
        except RenderMismatchError as e:
            logger.warning(str(e))
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=False,
                error=str(e),
            )
        except TransformMismatchError as e:
            logger.error(str(e))
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=False,
                error=str(e),
            )
        except Exception as e:
            logger.error(
                f"Failed to pull ink for {vault.name}:{obsidian_path}: {e}",
                exc_info=True,
            )
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=False,
                error=str(e),
            )

    def _pull_file_inner(
        self,
        vault: VaultConfig,
        obsidian_path: str,
    ) -> PullResult:
        """Inner pull logic (exceptions propagate)."""
        # Step 1: Load sync state
        sync_state = self.state.get_file_state(vault.name, obsidian_path)
        if sync_state is None:
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=True,
            )

        if sync_state.pushed_pdf_sha256 is None or sync_state.word_map is None:
            logger.debug(f"No pushed PDF state for {vault.name}:{obsidian_path}, skipping pull")
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=True,
            )

        doc_uuid = sync_state.remarkable_uuid
        word_map = WordMap.from_json(sync_state.word_map)

        # Step 2a: Verify document exists in cloud
        doc_index_hash = self.cloud_reader.get_document_index_hash(doc_uuid)
        if doc_index_hash is None:
            logger.debug(f"Document {doc_uuid} not found in cloud, skipping pull")
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=True,
            )

        # Step 2b: I10 — compare the .pdf entry's hash with pushed_pdf_sha256.
        # This ensures ink is interpreted only against the PDF we pushed.
        index_entries = self.cloud_reader.get_document_index_entries(doc_uuid)
        if index_entries is not None:
            pdf_entry_name = f"{doc_uuid}.pdf"
            pdf_hash: str | None = None
            for entry in index_entries:
                if entry.entry_name == pdf_entry_name:
                    pdf_hash = entry.hash
                    break
            if pdf_hash is not None and pdf_hash != sync_state.pushed_pdf_sha256:
                raise RenderMismatchError(
                    doc_uuid=doc_uuid,
                    expected_hash=sync_state.pushed_pdf_sha256,
                    actual_hash=pdf_hash,
                )

        # Download .content to get cPages
        content_bytes = self.cloud_reader.download_content_file(doc_uuid)
        if content_bytes is None:
            logger.warning(f"Could not download .content for {doc_uuid}")
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=True,
            )

        content_data = json.loads(content_bytes)
        cpages_raw = content_data.get("cPages", content_data)
        if isinstance(cpages_raw, dict):
            cpages_list = cpages_raw.get("pages", [])
        elif isinstance(cpages_raw, list):
            cpages_list = cpages_raw
        else:
            cpages_list = []

        if not cpages_list:
            logger.debug(f"No cPages entries for {doc_uuid}")
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=True,
            )

        # Step 3: Download .rm files
        temp_dir = self.cache_dir / "pull" / doc_uuid
        temp_dir.mkdir(parents=True, exist_ok=True)

        page_uuids = [str(p.get("id", "")) for p in cpages_list if p.get("id")]
        rm_file_paths = self.cloud_reader.download_page_rm_files(doc_uuid, page_uuids, temp_dir)

        rm_files: dict[str, Path] = {}
        for page_uuid, rm_path in zip(page_uuids, rm_file_paths):
            if rm_path is not None and rm_path.exists():
                rm_files[page_uuid] = rm_path

        if not rm_files:
            logger.debug(f"No .rm files found for {doc_uuid}")
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=True,
            )

        # Step 4: Extract candidates, cluster, classify
        known_ids = self.state.get_known_stroke_ids(vault.name, obsidian_path)

        candidates = extract_candidates(rm_files, cpages_list, known_ids)
        glyph_highlights = classify_glyph_highlights(rm_files, cpages_list, known_ids)

        # I8: Cross-check every GlyphRange's transformed rects against the word map.
        for gh in glyph_highlights:
            device_rects = [DeviceRect(x=r[0], y=r[1], w=r[2], h=r[3]) for r in gh.device_rects]
            result = check_i8(gh.text, device_rects, word_map, self._transform, gh.page)
            if not result.i8_pass:
                raise TransformMismatchError(
                    page=gh.page,
                    expected_words=result.glyph_text_words,
                    found_words=result.words_under_rects,
                )

        if not candidates and not glyph_highlights:
            logger.debug(f"No new ink for {vault.name}:{obsidian_path}")
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=True,
            )

        # Use the persisted content_page_count from the push-time render;
        # fall back to len(pages) for word maps serialised before this field.
        content_page_count = word_map.content_page_count or len(word_map.pages)
        clusters = cluster_candidates(candidates)

        classifications: list[Classification] = []
        for cluster in clusters:
            classification = classify_cluster(
                cluster, word_map, self._transform, content_page_count
            )
            classifications.append(classification)

        # Read snapshot text for edit computation
        snapshot_text = ""
        snapshot_bytes = self.state.snapshots.restore_file(vault.name, obsidian_path)
        if snapshot_bytes is not None:
            snapshot_text = snapshot_bytes.decode("utf-8")
        if not snapshot_text:
            file_path = vault.path / obsidian_path
            if file_path.exists():
                snapshot_text = file_path.read_text(encoding="utf-8")

        # Build edits from classifications and highlights
        edits: list[MarkdownEdit] = []
        dispositions: dict[str, Disposition] = {}
        deferred_reasons: list[str] = []
        # Overrides for stroke -> cluster_id mapping when gesture processing
        # splits a cluster into consumed + leftover parts (fix 4).
        stroke_cluster_overrides: dict[str, str] = {}

        # Build token→snapshot-character-span index (once, shared by all highlights).
        token_spans = build_token_spans(word_map.texts, snapshot_text) if snapshot_text else []

        # Process glyph highlights — use glyph_id for stable identity.
        # Resolve each highlight's character span via the word map so
        # apply_edits wraps at the correct position (not the first substring match).
        highlight_fallback_count = 0
        for gh in glyph_highlights:
            cluster_id = gh.glyph_id

            # GlyphRange rects → word-map tokens → snapshot span
            span = _resolve_highlight_span(
                gh,
                word_map,
                self._transform,
                token_spans,
            )
            if span is None:
                highlight_fallback_count += 1

            edit = MarkdownEdit(
                kind="highlight",
                cluster_id=cluster_id,
                anchor=InkAnchor(kind="paragraph", ref=""),
                text=gh.text,
                snapshot_span=span,
            )
            dispositions[cluster_id] = Highlight(edit=edit)
            edits.append(edit)

        if highlight_fallback_count:
            logger.info(
                f"{highlight_fallback_count} highlight(s) fell back to substring matching "
                f"(no word-map tokens found for rects)"
            )

        # Process stroke clusters
        for classification in classifications:
            cluster = classification.cluster
            cid = cluster.cluster_id

            if classification.kind == "appended":
                anchor = DOCUMENT_END
                ocr_text = self._ocr_cluster(cluster, vault.name, obsidian_path)
                if ocr_text is not None:
                    edit = MarkdownEdit(
                        kind="append",
                        cluster_id=cid,
                        anchor=anchor,
                        text=ocr_text,
                    )
                    dispositions[cid] = Consumed(edit_id=str(uuid_module.uuid4()), edit=edit)
                    edits.append(edit)
                else:
                    reason = "ocr_unavailable"
                    deferred_reasons.append(reason)
                    embed_name = (
                        f"{Path(obsidian_path).stem}-drawing-p{cluster.redir or 0}-{cid[:8]}.svg"
                    )
                    edit = MarkdownEdit(
                        kind="deferred",
                        cluster_id=cid,
                        anchor=anchor,
                        text=(
                            f"<!-- RPS:UNINTERPRETED kind=appended page={cluster.redir or 0} -->\n"
                            f"![[{embed_name}]]\n"
                            f"<!-- RPS:END -->"
                        ),
                    )
                    dispositions[cid] = Deferred(reason=reason, edit=edit)
                    edits.append(edit)
                    self._save_drawing(cluster, vault, obsidian_path, anchor, None)

            elif classification.kind == "drawing":
                # Try gesture classification before baking as a drawing.
                cluster_page = cluster.redir if cluster.redir is not None else 0
                gesture = classify_gesture(
                    cluster,
                    word_map,
                    self._transform,
                    cluster_page,
                    token_spans,
                    self.ink_config.strikethrough_action,
                    snapshot_text,
                )

                if gesture.kind != "none":
                    self._process_gesture(
                        gesture,
                        cluster,
                        cid,
                        word_map,
                        vault,
                        obsidian_path,
                        cluster_page,
                        token_spans,
                        edits,
                        dispositions,
                        deferred_reasons,
                        classification,
                        stroke_cluster_overrides,
                    )
                else:
                    # No gesture matched -> bake as drawing
                    anchor = _anchor_from_classification(classification)

                    attachment_name, svg_bytes, overlay = bake_cluster(
                        cluster,
                        self._transform,
                        anchor,
                        classification.nearest_anchor_rect,
                        Path(obsidian_path).stem,
                    )

                    edit = MarkdownEdit(
                        kind="embed",
                        cluster_id=cid,
                        anchor=anchor,
                        text=f"![[{attachment_name}]]",
                    )
                    dispositions[cid] = Baked(
                        attachment=attachment_name,
                        anchor=anchor,
                        svg=svg_bytes,
                        edit=edit,
                    )
                    edits.append(edit)
                    self._save_attachment(vault, attachment_name, svg_bytes)

        # Read current file
        file_path = vault.path / obsidian_path
        if not file_path.exists():
            return PullResult(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                success=False,
                error=f"File not found: {file_path}",
            )
        current_text = file_path.read_text(encoding="utf-8")

        # Compute all DB rows before touching the file (foreseeable conflict
        # check — I12 crash semantics still hold: attachments → markdown →
        # transaction, but a detectable duplicate aborts before the write).
        stroke_rows: list[tuple[str, str, str, str, str]] = []
        for cluster in clusters:
            cid = cluster.cluster_id
            for cs in cluster.strokes:
                sid = f"{cs.stroke.stroke_id.part1}:{cs.stroke.stroke_id.part2}"
                # Use the override cluster_id if this stroke was routed to
                # a leftover cluster by gesture processing (fix 4).
                effective_cid = stroke_cluster_overrides.get(sid, cid)
                stroke_rows.append((vault.name, obsidian_path, doc_uuid, sid, effective_cid))

        for gh in glyph_highlights:
            stroke_rows.append((vault.name, obsidian_path, doc_uuid, gh.glyph_id, gh.glyph_id))

        # Check for foreseeable conflicts: any stroke_id already in the DB
        # means we would fail inside the transaction after writing markdown.
        if stroke_rows:
            conflict_ids = known_ids & frozenset(r[3] for r in stroke_rows)
            if conflict_ids:
                logger.warning(
                    f"Foreseeable conflict: {len(conflict_ids)} stroke id(s) already "
                    f"in ledger for {vault.name}:{obsidian_path}; skipping write"
                )
                return PullResult(
                    vault_name=vault.name,
                    obsidian_path=obsidian_path,
                    success=True,
                )

        # Apply edits (step b of I12)
        if edits:
            new_text, failed_ids = apply_edits(
                edits, snapshot_text, current_text, self.layout.notes_heading
            )

            for fid in failed_ids:
                if fid in dispositions:
                    old_disp = dispositions[fid]
                    edit_for_defer = (
                        old_disp.edit
                        if hasattr(old_disp, "edit")
                        else MarkdownEdit(
                            kind="deferred",
                            cluster_id=fid,
                            anchor=DOCUMENT_END,
                            text="",
                        )
                    )
                    dispositions[fid] = Deferred(reason="patch_failed", edit=edit_for_defer)
                    deferred_reasons.append("patch_failed")

            write_markdown_atomic(file_path, new_text)

        # Step c: ONE SQLite transaction
        all_terminal = all(is_terminal(d) for d in dispositions.values())

        with self.state.conn:
            if stroke_rows:
                self.state.insert_ink_strokes(stroke_rows)

            now = int(time.time())
            for cid, disp in dispositions.items():
                disp_name = type(disp).__name__.lower()
                deferred_reason = disp.reason if isinstance(disp, Deferred) else None
                anchor_kind = None
                anchor_ref = None
                attachment = None
                edit_id = None
                rect_json = None

                if isinstance(disp, Highlight):
                    anchor_kind = disp.edit.anchor.kind
                    anchor_ref = disp.edit.anchor.ref
                elif isinstance(disp, Consumed):
                    edit_id = disp.edit_id
                    anchor_kind = disp.edit.anchor.kind
                    anchor_ref = disp.edit.anchor.ref
                elif isinstance(disp, Baked):
                    attachment = disp.attachment
                    anchor_kind = disp.anchor.kind
                    anchor_ref = disp.anchor.ref
                elif isinstance(disp, Deferred):
                    anchor_kind = disp.edit.anchor.kind
                    anchor_ref = disp.edit.anchor.ref

                disp_page_redir: int | None = None
                for cluster in clusters:
                    if cluster.cluster_id == cid:
                        disp_page_redir = cluster.redir
                        rect_json = json.dumps(
                            {
                                "x0": cluster.bbox[0],
                                "y0": cluster.bbox[1],
                                "x1": cluster.bbox[2],
                                "y1": cluster.bbox[3],
                            }
                        )
                        break

                self.state.insert_ink_cluster(
                    cluster_id=cid,
                    vault_name=vault.name,
                    obsidian_path=obsidian_path,
                    disposition=disp_name,
                    deferred_reason=deferred_reason,
                    page_redir=disp_page_redir,
                    rect=rect_json,
                    anchor_kind=anchor_kind,
                    anchor_ref=anchor_ref,
                    attachment=attachment,
                    edit_id=edit_id,
                    pushed_pdf_sha256=sync_state.pushed_pdf_sha256,
                    created_at=now,
                )

            pull_state = PullState(
                vault_name=vault.name,
                obsidian_path=obsidian_path,
                remarkable_uuid=doc_uuid,
                last_annotation_hash=doc_index_hash,
                last_pull_time=now,
                last_rm_files_hash=None,
            )
            self.state.update_pull_state(pull_state)

            if all_terminal and dispositions:
                self.state.update_ink_clearance_hash(vault.name, obsidian_path, doc_index_hash)

        highlight_count = sum(1 for d in dispositions.values() if isinstance(d, Highlight))
        notes_count = sum(1 for d in dispositions.values() if isinstance(d, Consumed))
        drawings_count = sum(1 for d in dispositions.values() if isinstance(d, Baked))
        deferred_count = sum(1 for d in dispositions.values() if isinstance(d, Deferred))

        # Count gestures by kind from Consumed edits
        tick_count = sum(
            1 for d in dispositions.values() if isinstance(d, Consumed) and d.edit.kind == "tick"
        )
        strikethrough_count = sum(
            1
            for d in dispositions.values()
            if isinstance(d, Consumed) and d.edit.kind == "strikethrough"
        )
        insertion_count = sum(
            1
            for d in dispositions.values()
            if isinstance(d, Consumed) and d.edit.kind == "insertion"
        )
        correction_count = sum(
            1
            for d in dispositions.values()
            if isinstance(d, Consumed) and d.edit.kind == "correction"
        )
        # Gestures that ended up as Baked (UNINTERPRETED)
        gestures_uninterpreted = sum(
            1
            for d in dispositions.values()
            if isinstance(d, Baked) and d.edit.kind == "deferred" and "kind=" in d.edit.text
        )

        return PullResult(
            vault_name=vault.name,
            obsidian_path=obsidian_path,
            success=True,
            highlights_added=highlight_count,
            notes_appended=notes_count,
            drawings_saved=drawings_count,
            deferred_count=deferred_count,
            highlight_fallback_count=highlight_fallback_count,
            deferred_reasons=deferred_reasons if deferred_reasons else None,
            ticks=tick_count,
            strikethroughs=strikethrough_count,
            insertions=insertion_count,
            corrections=correction_count,
            gestures_uninterpreted=gestures_uninterpreted,
        )

    def _process_gesture(
        self,
        gesture: GestureResult,
        cluster: InkCluster,
        cid: str,
        word_map: WordMap,
        vault: VaultConfig,
        obsidian_path: str,
        page_redir: int,
        token_spans: list[tuple[int, int]],
        edits: list[MarkdownEdit],
        dispositions: dict[str, Disposition],
        deferred_reasons: list[str],
        classification: Classification,
        stroke_cluster_overrides: dict[str, str] | None = None,
    ) -> None:
        """Process a recognised gesture into edits and dispositions.

        Handles OCR for insertion/correction gestures, low-confidence
        fallback to UNINTERPRETED, leftover stroke baking, and the
        ``already_checked`` deferred case.
        """
        anchor = _anchor_from_classification(classification)

        # Checkbox already checked -> Deferred
        if gesture.checkbox_already_checked:
            edit = MarkdownEdit(
                kind="deferred",
                cluster_id=cid,
                anchor=anchor,
                text="",
            )
            dispositions[cid] = Deferred(reason="already_checked", edit=edit)
            deferred_reasons.append("already_checked")
            return

        # OCR-based gestures (caret insertion, correction)
        replacement_text = gesture.replacement_text
        if gesture.needs_ocr:
            ocr_text = self._ocr_gesture_strokes(gesture.ocr_strokes)
            if ocr_text is None:
                # OCR unavailable -> Deferred
                edit = MarkdownEdit(
                    kind="deferred",
                    cluster_id=cid,
                    anchor=anchor,
                    text="",
                )
                dispositions[cid] = Deferred(reason="ocr_unavailable", edit=edit)
                deferred_reasons.append("ocr_unavailable")
                return

            ocr_confidence = (
                getattr(ocr_text, "confidence", 1.0) if not isinstance(ocr_text, str) else 1.0
            )
            text_value = ocr_text.text if hasattr(ocr_text, "text") else str(ocr_text)

            confidence_threshold = self.ocr_confidence_threshold

            if ocr_confidence < confidence_threshold:
                # Low confidence -> bake as UNINTERPRETED
                gesture_kind_str = gesture.kind.replace("_", " ")
                embed_name = f"{Path(obsidian_path).stem}-drawing-p{page_redir}-{cid[:8]}.svg"
                marker_text = (
                    f"<!-- RPS:UNINTERPRETED kind={gesture_kind_str} "
                    f"confidence={ocr_confidence:.2f} page={page_redir} -->\n"
                    f"![[{embed_name}]]\n"
                    f"<!-- RPS:END -->"
                )
                edit = MarkdownEdit(
                    kind="deferred",
                    cluster_id=cid,
                    anchor=anchor,
                    text=marker_text,
                )
                attachment_name, svg_bytes, overlay = bake_cluster(
                    cluster,
                    self._transform,
                    anchor,
                    classification.nearest_anchor_rect,
                    Path(obsidian_path).stem,
                )
                dispositions[cid] = Baked(
                    attachment=attachment_name,
                    anchor=anchor,
                    svg=svg_bytes,
                    edit=edit,
                )
                edits.append(edit)
                self._save_attachment(vault, attachment_name, svg_bytes)
                return

            # Build the final replacement text
            if gesture.kind == "caret_insertion":
                replacement_text = f" {text_value} "
            elif gesture.kind == "correction":
                replacement_text = f"{replacement_text}{text_value}"

        # Build the edit
        edit_kind: str
        if gesture.kind == "tick":
            edit_kind = "tick"
        elif gesture.kind == "strikethrough":
            edit_kind = "strikethrough"
        elif gesture.kind == "caret_insertion":
            edit_kind = "insertion"
        elif gesture.kind == "correction":
            edit_kind = "correction"
        else:
            edit_kind = "deferred"

        edit = MarkdownEdit(
            kind=edit_kind,  # type: ignore[arg-type]
            cluster_id=cid,
            anchor=anchor,
            text=replacement_text or "",
            snapshot_span=gesture.snapshot_span,
        )
        dispositions[cid] = Consumed(edit_id=str(uuid_module.uuid4()), edit=edit)
        edits.append(edit)

        # Additional contiguous-run edits (non-adjacent strikethrough/correction)
        for extra in gesture.extra_edits:
            extra_edit = MarkdownEdit(
                kind=edit_kind,  # type: ignore[arg-type]
                cluster_id=cid,
                anchor=anchor,
                text=extra.replacement_text,
                snapshot_span=extra.snapshot_span,
            )
            edits.append(extra_edit)

        # Bake leftover strokes as a separate drawing cluster
        if gesture.leftover_strokes:
            from rock_paper_sync.ink.gestures import _derive_cluster_id, _stroke_id_str

            leftover_ids = [_stroke_id_str(s) for s in gesture.leftover_strokes]
            leftover_cid = _derive_cluster_id(leftover_ids)

            # Record overrides so stroke_rows uses leftover_cid for these strokes
            if stroke_cluster_overrides is not None:
                for sid in leftover_ids:
                    stroke_cluster_overrides[sid] = leftover_cid
            leftover_cluster = InkCluster(
                cluster_id=leftover_cid,
                strokes=tuple(gesture.leftover_strokes),
                bbox=cluster.bbox,  # approximate
                page_index=cluster.page_index,
                redir=cluster.redir,
            )
            leftover_anchor = _anchor_from_classification(classification)
            attachment_name, svg_bytes, overlay = bake_cluster(
                leftover_cluster,
                self._transform,
                leftover_anchor,
                classification.nearest_anchor_rect,
                Path(obsidian_path).stem,
            )
            leftover_edit = MarkdownEdit(
                kind="embed",
                cluster_id=leftover_cid,
                anchor=leftover_anchor,
                text=f"![[{attachment_name}]]",
            )
            dispositions[leftover_cid] = Baked(
                attachment=attachment_name,
                anchor=leftover_anchor,
                svg=svg_bytes,
                edit=leftover_edit,
            )
            edits.append(leftover_edit)
            self._save_attachment(vault, attachment_name, svg_bytes)

    def _ocr_gesture_strokes(
        self,
        strokes: tuple[CandidateStroke, ...],
    ) -> object | None:
        """OCR a set of gesture text strokes.

        Renders them as a synthetic cluster and sends to OCR.
        Returns the raw OCR result (with ``.text`` and optionally
        ``.confidence`` attributes), a ``str``, or ``None`` if OCR is
        unavailable.  ``_process_gesture`` extracts text and confidence
        from the returned object.

        Raises:
            Any exception other than ``OCRServiceError`` — those are bugs,
            not service outages, and must not be silently deferred.
        """
        from rock_paper_sync.ocr.protocol import OCRServiceError

        if self.ocr_processor is None or not strokes:
            return None
        try:
            # Create a synthetic cluster for rendering
            from rock_paper_sync.ink.gestures import _derive_cluster_id, _stroke_id_str

            ids = [_stroke_id_str(s) for s in strokes]
            synth_cluster = InkCluster(
                cluster_id=_derive_cluster_id(ids),
                strokes=tuple(strokes),
                bbox=strokes[0].stroke.bounding_box,  # approximate
                page_index=strokes[0].page_index,
                redir=strokes[0].redir,
            )
            png_bytes = render_cluster_to_png(synth_cluster, self._transform)
            if hasattr(self.ocr_processor, "process_image"):
                result = self.ocr_processor.process_image(png_bytes)
                if result is not None:
                    return result
            return None
        except OCRServiceError:
            logger.warning("OCR service unavailable for gesture strokes", exc_info=True)
            return None

    def _ocr_cluster(
        self,
        cluster: InkCluster,
        vault_name: str,
        obsidian_path: str,
    ) -> str | None:
        """OCR a cluster (render to PNG, send to OCR service).

        Returns OCR text or None if OCR is unavailable or fails.
        """
        if self.ocr_processor is None:
            return None

        try:
            png_bytes = render_cluster_to_png(cluster, self._transform)

            # Simple OCR interface: expects a process_image method
            if hasattr(self.ocr_processor, "process_image"):
                result = self.ocr_processor.process_image(png_bytes)
                if result and hasattr(result, "text"):
                    return result.text
                if isinstance(result, str):
                    return result
            return None
        except Exception:
            logger.warning(
                f"OCR failed for cluster {cluster.cluster_id[:8]}",
                exc_info=True,
            )
            return None

    # _find_anchor_for_drawing removed: anchor resolution now uses
    # Classification.nearest_anchor_ref from WordMap.blocks (BlockBox).
    # See _anchor_from_classification().

    def _save_drawing(
        self,
        cluster: InkCluster,
        vault: VaultConfig,
        obsidian_path: str,
        anchor: InkAnchor,
        anchor_rect: Rect | None,
    ) -> str | None:
        """Save a drawing cluster as an SVG attachment. Returns attachment name."""
        attachment_name, svg_bytes, _ = bake_cluster(
            cluster,
            self._transform,
            anchor,
            anchor_rect,
            Path(obsidian_path).stem,
        )
        self._save_attachment(vault, attachment_name, svg_bytes)
        return attachment_name

    def _save_attachment(
        self,
        vault: VaultConfig,
        attachment_name: str,
        svg_bytes: bytes,
    ) -> None:
        """Save an SVG attachment to the vault's attachment folder.

        Idempotent: if the file already exists with the same name, it is
        overwritten (same cluster id = same content).
        """
        attach_dir = get_attachment_folder(vault.path)
        attach_dir.mkdir(parents=True, exist_ok=True)
        attach_path = attach_dir / attachment_name
        attach_path.write_bytes(svg_bytes)
        logger.debug(f"Saved attachment: {attach_path}")

    def pull_vault(
        self,
        vault: VaultConfig,
    ) -> tuple[list[PullResult], PullStats]:
        """Pull ink for all synced files in a vault.

        Args:
            vault: Vault configuration.

        Returns:
            Tuple of (list of PullResult, PullStats).
        """
        stats = PullStats()
        results: list[PullResult] = []

        synced_files = self.state.get_all_synced_files(vault.name)
        stats.files_checked = len(synced_files)

        for record in synced_files:
            if record.pushed_pdf_sha256 is None:
                stats.files_skipped += 1
                continue

            result = self.pull_file(vault, record.obsidian_path)
            results.append(result)

            if result.success:
                if (
                    result.highlights_added > 0
                    or result.notes_appended > 0
                    or result.drawings_saved > 0
                    or result.deferred_count > 0
                ):
                    stats.files_updated += 1
                    stats.total_highlights += result.highlights_added
                    stats.total_notes += result.notes_appended
                    stats.total_drawings += result.drawings_saved
                    stats.total_deferred += result.deferred_count
                else:
                    stats.files_skipped += 1
            else:
                stats.files_errored += 1

        return results, stats


def _anchor_from_classification(classification: Classification) -> InkAnchor:
    """Build an ``InkAnchor`` from a classification's nearest block.

    Uses ``nearest_anchor_ref`` (from ``WordMap.blocks``) when available.
    Falls back to ``DOCUMENT_END``.
    """
    if classification.nearest_anchor_ref is not None:
        return InkAnchor(kind="paragraph", ref=classification.nearest_anchor_ref)
    return DOCUMENT_END


def _resolve_highlight_span(
    gh: GlyphHighlight,
    word_map: WordMap,
    transform: CanvasTransform,
    token_spans: list[tuple[int, int]],
) -> tuple[int, int] | None:
    """Resolve a highlight's character span in the snapshot via the word map.

    Transforms each ``GlyphRange`` device rect to PDF coords, finds the
    token at each rect's centre via ``WordMap.token_at``, then maps the
    contiguous token range to the snapshot character span using the
    pre-built ``token_spans`` index.

    Returns:
        ``(start, end)`` in snapshot text, or ``None`` if no tokens matched.
    """

    matched_tids: list[int] = []
    for rx, ry, rw, rh in gh.device_rects:
        dev_cx = rx + rw / 2.0
        dev_cy = ry + rh / 2.0
        pdf_x, pdf_y = device_to_pdf(transform, dev_cx, dev_cy)
        match = word_map.token_at(gh.page, pdf_x, pdf_y)
        if match is not None:
            matched_tids.append(int(match.token))

    if not matched_tids or not token_spans:
        return None

    # Find the contiguous span in the snapshot covering all matched tokens.
    valid_spans = [
        token_spans[tid]
        for tid in matched_tids
        if tid < len(token_spans) and token_spans[tid][0] >= 0
    ]
    if not valid_spans:
        return None

    start = min(s for s, _ in valid_spans)
    end = max(e for _, e in valid_spans)
    return (start, end)
