# PDF substrate plan

Status: APPROVED (architecture + design review, re-review 2026-09-19). Progress: Phase 1 done (e4cd45b), Phase 3a done (7ef09fe). Supersedes the native typed-text direction in
`docs/LAYOUT_VERIFICATION_PLAN.md`, `docs/DEVICE_REPLICA_DESIGN.md` and
Milestones 5.5/7 of `docs/PRODUCT_ROADMAP.md`.

## 1. Goal

Bidirectional sync between an Obsidian vault and a reMarkable Paper Pro
Move where:

- every markdown file is pushed to the device as a **PDF rendered
  locally** (not a typed-text notebook);
- ink drawn on the device comes back into the vault as highlights,
  OCR'd text, interpreted edits (strikethrough, caret insertion,
  checkbox tick, correction) or drawings saved as attachments;
- handwriting appended after the text (on blank trailing pages or pages
  the user adds on-device) becomes markdown text that the next push
  renders as print;
- the cloud transport (Sync v3 against rmfakecloud), state database,
  multi-vault config, parser and self-hosted OCR service are kept;
- the native typed-text layout work is isolated for a future project,
  not deleted;
- **no ink is ever lost silently.** Every stroke on the device ends up
  in the vault (as an edit, text, highlight or SVG) before anything
  removes it from the device.

## 2. Why (one paragraph)

A typed-text notebook anchors every stroke to a character offset that
only xochitl's Qt layout can turn into a position. Predicting that
layout consumed ~12k source lines, a calibration corpus and repeated
device sessions, and the recorded sessions still produced stacked
strokes (`docs/bugs/STROKE_SESSION_2026-07-05_FORENSICS.md`). With a
PDF we render, every glyph box is known exactly from our own layout
engine, ink is stored in absolute per-page coordinates with no text
anchors, and gesture interpretation becomes geometry against boxes we
produced. Firmware cannot change our layout.

## 3. Fixed decisions

| # | Decision | Rationale / evidence |
|---|----------|----------------------|
| D1 | Renderer: mistune AST → HTML → **WeasyPrint** → PDF. One vendored OFL font (must cover Latin + the glyphs we use; checkboxes are CSS-bordered boxes, not glyphs, so no U+2610 dependency). `weasyprint` and `pymupdf` become `pyproject.toml` dependencies; pango/cairo system requirement documented in README. WeasyPrint version pinned. | mistune is already the parser; CSS gives full control of interline gap (needed for insertions). |
| D2 | **Device profile** decides page geometry: `DeviceProfile(name, screen_w_px, screen_h_px)`; PDF page size in points = screen px × 0.5. Profiles are a dict constant in `device_profile.py`. Phase 1 ships `paper_pro_move` (954×1696, **unverified until Phase 2**); `paper_pro` and `remarkable_2` are added when someone verifies them. | Matching aspect ratio avoids letterboxing; the transform is one scale factor plus the centre-origin shift `.rm` files use. |
| D3 | **Word map comes from WeasyPrint's box tree, not from PDF text extraction.** Every parser token is wrapped in `<span data-token="N">`; after layout the renderer walks each page's box tree and records the token's rects. A token may yield several boxes (`overflow-wrap: anywhere` for long URLs/code): `rect_of` returns a non-empty tuple, contiguous in reading order. Token→rects is true by construction. PyMuPDF is used only by the bench overlay. Highlights: the parser enables mistune's `mark` plugin so `==text==` becomes a `<mark>` with a CSS background (the device shows the highlight after re-push; the marked words stay ordinary tokens for I8). | Whitespace-split PDF words cannot be aligned totally (list markers, ligatures, code spacing). |
| D4 | Ink read via existing `rmscene`/`RmFileExtractor`; strokes and `GlyphRange` highlights converted device px → PDF pt by the profile transform. Page identity is the `cPages[i].redir.value` (PDF page index), never list position. | Existing code; device-inserted pages shift positions. |
| D5 | **Push = same document UUID, new PDF, no `.rm` files**, guarded by an ink clearance (I9). Content file uses rmapi's proven minimal shape: `fileType:"pdf"`, `pages:[""]`, `pageCount:0`, empty `.pagedata`, `UUID.pdf` blob. Device builds `cPages` itself. Same-UUID is the only implemented path; if Phase 2 falsifies it, new-UUID (delete + create in one staged root update) *replaces* it rather than becoming a fallback. | rmapi has uploaded PDFs this way for years. Whether the device re-initialises `cPages` on a same-UUID re-push, and whether it accepts a non-monotonic metadata `version`, is verified in Phase 2. |
| D6 | Renderer appends `trailing_blank_pages` (default 2) ruled pages. Ink on those pages, and on device-inserted pages (`cPages` entries without `redir`), is **appended handwriting**: a page inserted mid-document anchors after the last token of the preceding `redir` page; trailing pages anchor at `document_end`. | Device supports "Add page" in PDFs; blank trailing pages mean the user never has to. |
| D7 | Every ink **cluster** gets one `Disposition` (a sum type): `Highlight`, `Consumed(edit_id)` (gesture or appended text → markdown; strokes dropped on next push), `Baked(attachment, anchor)` (SVG in vault, drawn into the next PDF), `Deferred(reason)` (non-terminal: OCR unavailable, patch failed to apply). The ledger is per **stroke id**, not per cluster (I4). | No `.rm` file is ever rewritten; cluster membership is unstable across pulls, stroke ids are not. |
| D8 | Gestures (Phase 5): checkbox tick, strikethrough, caret insertion with interline text, correction (= strikethrough + insertion). Below confidence threshold → `Baked` + `UNINTERPRETED` marker block. | All are box tests against the word map. |
| D9 | **Native layout isolated**: `src/rock_paper_sync/layout/`, `font_metrics.py`, `fonts/device/`, `tools/rmlib/`, `tools/calibration/`, calibration corpus and `LAYOUT_*`/replica docs move under `native_layout/` (not packaged, not in default `pytest`), then `git subtree split` to their own repo. The seam in §4 stays in-tree. | User wants it reusable for a future native-notebook project. |
| D10 | OCR service interface unchanged (image in, text out; TrOCR + LoRA, self-hosted). Text-only post-correction via the local OpenAI-compatible endpoint is Phase 6 and adds no config field before then. | Privacy; fine-tuning on own handwriting. |
| D11 | Transport unchanged. Harness rmfakecloud smoke test: 15/15 pass (2026-09-19). Pull receives a **read-only `CloudReader` protocol** (index + download methods only), never `RmCloudSync`. | User constraint; I3 by type. |

## 4. The seam

```python
TokenId = NewType("TokenId", int)          # reading-order index into the pushed snapshot's token list

@dataclass(frozen=True)
class Rect: x0: float; y0: float; x1: float; y1: float   # PDF points, top-left origin

@dataclass(frozen=True)
class TokenMatch:
    token: TokenId
    page: int                # PDF page index (== cPages redir value)
    rect: Rect
    text: str
    x_fraction: tuple[float, float]   # (0.0, 1.0) for a whole-token hit; partial for Phase 5 strikethrough

@dataclass(frozen=True)
class PageWords:
    page: int
    tokens: tuple[tuple[TokenId, Rect], ...]

@dataclass(frozen=True)
class WordMap:
    page_size: tuple[float, float]
    pages: tuple[PageWords, ...]
    texts: tuple[str, ...]                        # index = TokenId
    def token_at(self, page: int, x: float, y: float) -> TokenMatch | None: ...
    def rects_of(self, token: TokenId) -> tuple[TokenMatch, ...]: ...   # non-empty; raises KeyError on unknown id
    def to_json(self) -> bytes: ...
    @classmethod
    def from_json(cls, data: bytes) -> "WordMap": ...

@dataclass(frozen=True)
class BakedOverlay:
    anchor: InkAnchor                # paragraph fingerprint or document_end
    offset: Rect                     # relative to the anchor paragraph's box
    svg: bytes

@dataclass(frozen=True)
class RenderedDocument:
    pdf: bytes
    pdf_sha256: str
    word_map: WordMap
    placements: tuple[Placed | Relocated, ...]   # one per overlay, total (I11)

class DocumentRenderer(Protocol):
    def render(self, doc: MarkdownDocument, profile: DeviceProfile,
               overlays: Sequence[BakedOverlay] = ()) -> RenderedDocument: ...
```

Everything downstream of `WordMap` (cluster classification, gesture
interpretation, OCR, markdown edits, dispositions) is backend-agnostic.
The PDF backend builds `WordMap` from WeasyPrint's box tree; a native
backend would build it from a layout replica. Overlay *placement* is the
renderer's job (it owns geometry); `ink/` only produces overlays.

**Provenance (I10):** the `WordMap` and `pdf_sha256` are persisted on
`sync_state` at push, in the same transaction as the state update.
Pull loads them and refuses to interpret ink unless the device's
document index lists `{uuid}.pdf` with that exact hash.

## 5. Module map

New (all under `src/rock_paper_sync/`):

| Module | Responsibility |
|--------|----------------|
| `device_profile.py` | `DeviceProfile`, the profile dict, `device_to_pdf` / `pdf_to_device`. The only file allowed to contain screen-pixel literals. |
| `render/html.py` | mistune AST → HTML: `data-token` spans in reading order, `<mark>` for `==text==`, CSS-bordered checkboxes, trailing ruled pages, overlay `<img>` placement, an "orphaned ink" trailing page when a `Relocated` placement exists. |
| `parser.py` | Enable mistune `mark` plugin (Phase 1, with a fixture containing `==text==`). |
| `render/pdf.py` | WeasyPrint render with vendored font + CSS; box-tree walk → `WordMap`; `PdfRenderer` implements `DocumentRenderer`. |
| `render/wordmap.py` | The frozen types in §4 and JSON (de)serialisation. Imports nothing from `weasyprint`, `fitz`, or the cloud modules. |
| `cloud_reader.py` | `CloudReader` protocol: `get_document_index(uuid)`, `download_blob(hash)`. `RmCloudSync` satisfies it structurally. |
| `ink/candidates.py` | Device strokes for a document minus the `ink_strokes` ledger → candidate strokes. |
| `ink/clusters.py` | Thin wrapper over `annotations/domain/stroke.py`, `stroke_cluster.py` and `common/spatial.py` (kept; they import only rmscene). |
| `ink/classify.py` | Cluster + `WordMap` → {highlight, appended, gesture:<kind>, drawing}. |
| `ink/gestures.py` | Phase 5 interpreters → `MarkdownEdit`s (computed against the pushed snapshot text). |
| `ink/bake.py` | Cluster → SVG bytes + `BakedOverlay`; attachment naming; never imports the renderer. |
| `ink/apply.py` | Applies `MarkdownEdit`s to the *current* vault file with diff-match-patch `patch_apply` against the pushed snapshot; a patch that fails → the cluster is `Deferred("patch_failed")`. |

Changed:

| Module | Change |
|--------|--------|
| `rm_cloud_sync.py` | `upload_pdf_document(payload: PdfDocumentPayload, clearance: InkClearance)`; `PdfDocumentPayload` is a frozen dataclass with exactly `metadata, content, pagedata, pdf` and a `.to_files()` (I2 by construction). Pre-merge re-reads `get_document_index_hash(uuid)` and raises `InkNotPulledError` if it differs from `clearance.doc_index_hash` (I9). `mint_clearance_if_no_ink(uuid)` probes the index for `.rm` entries. |
| `converter.py` | Push path: render → write `sync_state.status='pushing', pending_pdf_sha256, word_map` → upload → flip to `synced`. On start-up, `pushing` rows reconcile against the cloud doc index. Notebook push path deleted in Phase 3b. |
| `pull_sync.py` | Takes `CloudReader` + `StateManager`; order per pull: (1) write attachments (content-addressed, idempotent), (2) atomic-replace markdown (temp + `os.replace`), (3) one SQLite transaction inserting `ink_strokes`, `ink_clusters`, `pull_state`, and — only if every cluster is terminal — the `InkClearance`. A crash between (2) and (3) produces a visible duplicate on re-pull, never invisible loss. Process lock: `fcntl.flock` on the state DB path for the whole `sync` run. |
| `state.py` | Schema **v9**: `sync_state` gains `status`, `pending_pdf_sha256`, `pushed_pdf_sha256`, `word_map BLOB`, `ink_clearance_hash`; new `ink_strokes(vault, path, doc_uuid, stroke_id, cluster_id, PRIMARY KEY(vault, path, stroke_id))`; new `ink_clusters(cluster_id PK, vault, path, disposition, deferred_reason, page_redir, rect, anchor_kind, anchor_ref, attachment, edit_id, pushed_pdf_sha256, created_at)`. `anchor_kind ∈ {paragraph, document_end}`; `anchor_ref` = paragraph text hash + ordinal among duplicates. Phase 3b drops `orphaned_annotations`, `paragraph_state`; keeps `pull_state`, `ocr_results`, `ocr_corrections`, snapshots. |
| `ocr/integration.py` | `OCRProcessor` takes `(cluster_id, png, context_text)` items instead of `DocumentModel` + `annotation_map`. `OCRServiceError` → `Deferred("ocr_unavailable")`, never `Consumed`. |
| `annotation_sync_helper.py` | `compute_annotation_hash` reimplemented over sorted stroke ids from `SceneGraphIndex` (no layout). |
| `config.py` | `LayoutConfig` keeps its name and TOML section; fields become `device`, `font_size_pt` (11), `line_height` (1.4 multiplier), `margin_top_pt`/`margin_bottom_pt`/`margin_left_pt`/`margin_right_pt` (36), `trailing_blank_pages` (2), `notes_heading` ("Notes"). Old unit-less `margin_*` keys are read with a one-time warning. Device validation error lists the supported profile(s). |
| `cli.py` | No `--backend` flag. New `bench render <file> [--overlay]`. `--direction push` keeps the I9 check; `--discard-ink` is the only override and prints what it discards. Pull reports `N drawing(s) saved as attachments (not interpreted)` and `N cluster(s) deferred: <reason>` on stderr. |
| `pyproject.toml` | add `weasyprint` (pinned), `pymupdf`, `import-linter`; `justfile` with `check` (ruff, mypy, import-linter, pytest). |

Removed from production in Phase 3 (3a moves the layout engine, 3b moves the rest of the native backend):

- 3a (move to `native_layout/`): `layout/`, `font_metrics.py`, `fonts/device/`, `tools/rmlib/`, `tools/calibration/`, `tests/layout/`, layout-specific record/replay tests + goldens, `LAYOUT_*` / replica docs.
- 3b (move to `native_layout/backend/`, delete only what nothing under `native_layout/` uses): `generator.py` text-item generation, pagination, `_apply_annotations_to_page`, `_build_transform_plan`; `annotations/scene_adapter/{executor,translator,bundle}.py` (**`scene_index.py` and `block_registry.py` move to `annotations/common/`** — `stroke.py`, `stroke_cluster.py`, `rm_file_extractor.py` and `compute_annotation_hash` depend on `SceneGraphIndex`; import-linter pins them to rmscene-only), `annotations/services/crdt_service.py`, stroke re-anchoring in `annotations/handlers/stroke_handler.py`, `AnchorContext` migration; `coordinates.py` `AnchorResolver` and anchor-relative point types; `ocr/paragraph_mapper.py`; `annotations/common/text_extraction.py`; `RmCloudSync.upload_document` (notebook); the `src/rock_paper_sync/layout/` sentinel stubs left by 3a. Rationale: after 3a, 18 tests under `native_layout/` fail only because they reach these production modules through `rm_file_extractor`/`RemarkableGenerator`; moving them makes `native_layout/` the complete native backend the user wants to keep.

## 6. Vault conventions (what the user sees)

- **Attachments** go to the folder named by `.obsidian/app.json`
  `attachmentFolderPath` (vault root if unreadable), named
  `{stem}-drawing-p{page}-{cluster_id[:8]}.svg`, embedded as
  `![[{name}.svg]]` on its own line after the anchor paragraph. Same
  cluster id → no-op; new id at the same paragraph → new file, old kept.
- **Consumed gestures** leave no marker; the edit is the record. Counts
  are reported on stderr like today's highlight/stroke counts.
- **Uninterpreted / deferred ink**:
  ```markdown
  <!-- RPS:UNINTERPRETED kind=strikethrough confidence=0.42 page=3 -->
  ![[note-drawing-p3-a1b2c3d4.svg]]
  <!-- RPS:END -->
  ```
  Reuses the existing `RPS:` / `RPS:END` bracketing from `ocr/markers.py`;
  stripped before render like `strip_ocr_markers`.
- **Appended handwriting** is appended under a level-2 heading
  `## {notes_heading}` inserted once; later pulls append below it. Below
  the OCR confidence threshold the SVG embed is used instead.
- **Highlights** stay `==text==`.

## 7. Invariants (each names its enforcement point)

| # | Invariant | Enforcement |
|---|-----------|-------------|
| I1 | Every parser token owns at least one laid-out box, all boxes contiguous in reading order. | Renderer asserts per `data-token` span; test renders every sample fixture incl. an over-wide token. |
| I2 | A push payload cannot contain a `.rm`. | `PdfDocumentPayload` has four fields; `.to_files()` is the only producer. |
| I3 | Pull cannot write to the cloud. | `pull_sync` and `ink/*` accept `CloudReader` only; import-linter forbids `sync_v3`, `rm_cloud_sync` there. |
| I4 | A consumed stroke never re-enters a candidate set. | `ink_strokes` primary key; test: pull twice, add one stroke, pull again → only the new stroke is a candidate. |
| I5 | One stroke belongs to at most one cluster/disposition. | Same primary key; inserting a duplicate raises. |
| I6 | `pdf_to_device(device_to_pdf(p)) == p` within 0.5 px; profile aspect == page aspect. | Property test per profile. |
| I7 | Screen-pixel literals exist only in `device_profile.py`. | `tests/test_device_profile_guard.py` (re-homed literal guard, Phase 1). |
| I8 | Every pull cross-checks the transform. | Words under each device highlight's transformed rects must equal `GlyphRange.text`; mismatch → `TransformMismatchError`. |
| I9 | Push never discards un-pulled ink. | `upload_pdf_document` requires an `InkClearance`; pre-merge doc-index hash CAS raises `InkNotPulledError`. Only `--discard-ink` mints a forced clearance. |
| I10 | Ink is interpreted only against the PDF on the device. | Pull compares the doc index `.pdf` hash with `sync_state.pushed_pdf_sha256`; mismatch → `RenderMismatchError`. |
| I11 | Baked placement is total. | `render()` returns `Placed | Relocated(document_end)` per overlay; an unresolved anchor lands on the orphaned-ink page, never dropped. |
| I12 | Pull is crash-safe toward duplicates, not loss. | Write order in §5 `pull_sync`; test kills between steps 2 and 3. |

Accepted risk (documented, not solved): ink drawn on the device in the
seconds between a pull and the following push lands on page ids the new
`cPages` no longer contains. Same-UUID or not, it is unrecoverable; the
`flock` and pull-then-push ordering make the window small.

## 8. Phases

Each phase is one implementer task with its own verification loop.

**Phase 1 — PDF push (local only).** `device_profile`, `render/*`,
`WordMap` from the box tree, `PdfDocumentPayload`, `InkClearance` +
`mint_clearance_if_no_ink`, `upload_pdf_document`, `CloudReader`,
schema v9 columns on `sync_state`, config changes, `bench render`,
`justfile check`, import-linter contracts, literal guard. Converter
push path switches to PDF (notebook path left in place but unreachable
until 3b). Tests: I1, I2, I6, I7, I9 (index with `.rm` and no clearance
raises; stale clearance raises); rmfakecloud integration test that
pushes a PDF document and reads back the four index entries.

**Phase 3a — isolate native layout.** Move per D9; `uv run pytest
native_layout/` runs them; default suite green; `native_layout/README.md`
has the `git subtree split` recipe. Pull is **disabled** from here until
Phase 4 (imports `layout`); I9 makes push safe in that window because
no clearance can be minted for a document that has ink.

**Phase 2 — device-session package (prepared, NOT run).** A
record/replay test with a fixture covering paragraphs, headings, nested
lists, a checklist, code, a long paragraph (strikethrough), a short
paragraph (caret insertion), trailing pages. Online prompts: one
highlight, one margin note, one tick, one strikethrough, one caret
insertion, writing on a trailing page, adding a page via the device
menu, then a second same-UUID push with `--discard-ink` (D5 check,
metadata `version` check). Offline analysis overlays ink on the PDF,
reports measured scale/offset vs profile, checks I8 on the recorded
highlight, dumps `cPages`. **Stops and reports to the user before any
online run.**

**Phase 3b — move the notebook push path and re-anchoring code into
`native_layout/backend/`** (any time after the device opens our PDF; not a
prerequisite for Phase 4). Acceptance: `uv run pytest native_layout/` has no
failures caused by the split (the 18 from 3a included), and `src/` has no
sentinel stubs left.

**Phase 4 — pull on word boxes.** `ink/candidates`, `clusters`,
`classify` (highlight / appended / drawing), `bake`, `apply`,
`ink_strokes` + `ink_clusters`, `Disposition`, `Deferred` handling,
appended-page OCR → `## Notes`, re-push with overlays (I11), pull write
order + `flock`. Tests: I3, I4, I5, I8, I10, I11, I12; OCR mock raises
→ no clearance → push raises.

**Phase 5 — gestures.** Tick → strikethrough → caret insertion →
correction, each with synthetic-ink unit tests built from the word map,
plus the Phase 2 recording once it exists.

**Phase 6 (optional) — context post-correction** via the local LLM
endpoint, text-only; adds `OCRConfig.context_llm_url` then.

Order: 1 → 3a → 2 → **pause** → 4 → 5 → 6, with 3b any time after the
device confirms it opens our PDF. Phase 4 depends on 3a only; it can
proceed before the device session using the unverified Move profile.
The session tightens one scale factor and confirms D5.
