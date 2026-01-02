# Rock-Paper-Sync Product Roadmap

> **Document Status**: Draft for Product Manager Review
> **Last Updated**: 2026-01-02
> **Current State**: Milestone 5 Complete (Bidirectional Sync - Core)

## Executive Summary

This roadmap defines the path from the current one-way sync with annotation preservation to a fully bidirectional, zero-UI synchronization system between Obsidian and reMarkable Paper Pro.

### Vision Statement

*Invisible infrastructure that keeps your handwritten thoughts and typed words perfectly synchronized—no buttons, no conflicts, no friction.*

### Key Design Principles

1. **Markdown is flat**: Single-dimensional representation. Only stroke cluster annotations become footnotes. Highlights, additional text, and new paragraphs are inline content.
2. **Respect user layers**: User-created layers MUST be preserved and never destroyed. We may add system layers for preservation purposes (hidden originals, OCR text, etc.) alongside user content.
3. **1:1 document mapping**: Each Markdown file corresponds to exactly one reMarkable document, regardless of vault.
4. **Cloud-native**: All sync via cloud API—no device battery considerations.
5. **Strokes are writing**: Drawings are out of scope; classify all strokes as handwriting.

---

## Completed Milestones

### Milestone 1: Core Sync ✅
- Markdown → reMarkable document conversion
- Mistune-based parser with formatting preservation
- Multi-page document generation via rmscene
- SQLite state tracking with content hashing

### Milestone 2: Cloud Sync ✅
- Sync v3 protocol implementation (reverse-engineered)
- hashOfHashesV3 algorithm for document manifests
- CRDT formatVersion 2 metadata generation
- File deletion and update propagation

### Milestone 3: Multi-Vault ✅
- Multiple Obsidian vault configuration
- Per-vault reMarkable folder organization
- Vault-aware state management (schema v2)
- CLI filtering by vault name

### Milestone 4: Annotation System ✅
- Generation-based annotation detection
- AnchorContext V2 with content-based anchoring
- Three-way merge (content + annotations + new content)
- Stroke and highlight preservation across edits
- OCR integration with TrOCR backend

### Milestone 5: Bidirectional Sync (Core) ✅
- Pull sync: annotations from device → markdown (highlights as `==text==`, strokes as footnotes)
- Annotation change detection via hash comparison
- Reanchoring with AnchorContext.resolve() (confidence-based)
- Orphan handling: HTML comment + database tracking
- Unified `sync` command (pull-first by default, `--direction` flag)
- Deprecated `push` command (alias for `sync --direction push`)
- Database schema v7 with pull_state and orphaned_annotations tables

**Deferred to M5.5:**
- HiddenLayerManager for orphan preservation in .rm files
- Frontmatter badges for conflict state

---

## Future Milestones

### Milestone 5.5: Orphan Layer Management
**Goal**: Preserve orphaned annotations in hidden .rm file layer

#### 5.1 Change Detection (Device → Vault)
**Objective**: Detect changes made on the reMarkable device that need to sync back.

**Change Types**:

| Change Type | Detection Method | Sync Action |
|-------------|------------------|-------------|
| New annotations | Compare annotation hashes | Add to markdown |
| Deleted annotations | Missing from device | Remove from markdown |
| Modified annotations | Content hash mismatch | Update markdown |
| OCR corrections | Text layer edits | Update footnotes |
| New documents | Unknown UUID | Create markdown file |
| Deleted documents | Missing from device | Archive/delete markdown |

**Deliverables**:
- Device state polling / webhook integration
- Change manifest generation (what changed since last sync)
- Conflict detection (both sides modified)
- Change type classification

#### 5.2 Conflict Resolution
**Objective**: Handle simultaneous edits from Obsidian and reMarkable.

**Conflict Scenarios**:

1. **Paragraph edited in Obsidian + annotated on device**
   - Resolution: Merge using AnchorContext migration
   - Annotations reanchored to edited content

2. **Same paragraph edited on both sides** (rare with annotation sync)
   - Resolution: Prompt user or apply merge heuristics
   - Future: Three-way merge with common ancestor

3. **Document deleted on one side, modified on other**
   - Resolution: Preserve modifications, mark for user review

4. **Structural conflicts** (paragraphs reordered)
   - Resolution: Use spatial anchoring fallback

**Deliverables**:
- Conflict detection algorithm
- Merge strategy selection (auto-merge vs. user prompt)
- Conflict markers in markdown for unresolvable conflicts
- Conflict resolution UI hints (future: Obsidian plugin)

#### 5.3 Annotation → Markdown Rendering
**Objective**: Represent device annotations in Obsidian markdown.

**Rendering Strategies**:

| Annotation Type | Markdown Representation |
|-----------------|------------------------|
| Highlights | `==highlighted text==` or HTML comments |
| Margin strokes (OCR'd) | Footnotes `[^n]: text` |
| Inline strokes (OCR'd) | Inline text or callout blocks |
| Drawings/diagrams | Exported as images + link |
| Uncategorized strokes | HTML comments with stroke data |

**Deliverables**:
- Annotation → Markdown renderer per annotation type
- Configurable rendering style (inline vs. footnotes vs. comments)
- Image export for drawings (PNG with transparency)
- Round-trip preservation (markdown → device → markdown)

#### 5.4 Pull Sync Implementation
**Objective**: Implement the sync direction: reMarkable → Obsidian.

**Sync Algorithm**:
```
1. Fetch device document list
2. For each document with changes:
   a. Download .rm files
   b. Extract annotations
   c. Determine change type (new/modified/deleted)
   d. Check for conflicts with local markdown
   e. Apply merge strategy
   f. Write updated markdown
   g. Update state database
3. Handle orphaned documents (on device but not in vault)
```

**Deliverables**:
- Pull sync command: `rock-paper-sync pull [--vault NAME]`
- Incremental pull (only changed documents)
- Dry-run mode (show what would change)
- Pull conflict handling (merge or prompt)

#### 5.5 Unified Sync Orchestration
**Objective**: Seamless bidirectional sync with intelligent conflict handling.

**Sync Modes**:

| Mode | Behavior | Use Case |
|------|----------|----------|
| `push` | Obsidian → reMarkable only | Current behavior |
| `pull` | reMarkable → Obsidian only | Fetch annotations |
| `sync` | Bidirectional with merge | Normal operation |
| `mirror-push` | Obsidian is source of truth | Reset device state |
| `mirror-pull` | reMarkable is source of truth | Reset vault state |

**Deliverables**:
- Unified sync command: `rock-paper-sync sync`
- Direction selection: `--direction push|pull|both`
- Conflict mode: `--conflicts auto|prompt|ours|theirs`
- State reconciliation for interrupted syncs

---

### Milestone 6: Zero-UI Experience
**Goal**: Completely transparent sync requiring no user interaction

#### 6.1 Background Sync Daemon
**Objective**: Always-running service that syncs automatically.

**Architecture**:
```
[File System Watcher] → [Change Queue] → [Sync Engine]
         ↓                    ↓                ↓
  Obsidian edits         Debouncing      Cloud API
         ↓                    ↓                ↓
   inotify/FSEvents    Batch optimization   Rate limiting
```

**Deliverables**:
- Daemon mode: `rock-paper-sync daemon`
- Systemd service file for Linux
- launchd plist for macOS
- Windows service wrapper (future)
- Graceful shutdown with state preservation

#### 6.2 Intelligent Sync Triggers
**Objective**: Sync at optimal times without user intervention.

**Trigger Types**:

| Trigger | Condition | Action |
|---------|-----------|--------|
| File save | Markdown file saved | Queue for sync (debounced) |
| Idle detection | No edits for N seconds | Execute queued syncs |
| Network change | WiFi connected | Pull device changes |
| Device online | reMarkable comes online | Bidirectional sync |
| Scheduled | Cron-like intervals | Background sync |

**Deliverables**:
- Debounced file watching (500ms default)
- Idle detection (2s default before sync)
- Network state monitoring
- Device online detection via cloud API
- Configurable sync intervals

#### 6.3 Conflict-Free Sync Optimization
**Objective**: Minimize conflicts through smart sync timing.

**Strategies**:

1. **Annotation lock period**: After device annotation, wait before pushing updates
2. **Edit session detection**: Detect active Obsidian editing, defer pull
3. **Structural change batching**: Group related edits into single sync
4. **Predictive pre-sync**: Sync before likely conflict scenarios

**Deliverables**:
- Edit session heuristics (typing cadence, cursor movement)
- Annotation cooldown period (configurable, default 30s)
- Smart batch boundaries (paragraph-level grouping)
- Sync scheduling optimizer

#### 6.4 Status and Notification System
**Objective**: Inform user of sync status without requiring interaction.

**Status Channels**:

| Channel | Content | Platform |
|---------|---------|----------|
| System tray icon | Sync status indicator | All |
| Desktop notifications | Conflicts, errors | All |
| Log file | Detailed sync history | All |
| Status file | Machine-readable state | Tooling |
| Obsidian status bar | Plugin integration | Obsidian |

**Status States**:
- Synced (green checkmark)
- Syncing (animated)
- Pending (changes queued)
- Conflict (requires attention)
- Error (needs intervention)
- Offline (no network)

**Deliverables**:
- System tray integration (Linux/macOS/Windows)
- Desktop notification support
- Status file at `~/.local/share/rock-paper-sync/status.json`
- Obsidian plugin with status bar (future: separate project)

#### 6.5 Error Recovery and Self-Healing
**Objective**: Automatically recover from transient errors.

**Recovery Strategies**:

| Error Type | Recovery Action |
|------------|-----------------|
| Network timeout | Exponential backoff retry |
| API rate limit | Pause and retry with jitter |
| File locked | Retry after delay |
| Parse error | Skip file, log, continue |
| Conflict | Apply default resolution, log for review |
| State corruption | Rebuild from source of truth |

**Deliverables**:
- Retry logic with exponential backoff
- Rate limit detection and compliance
- State verification and repair
- Automatic recovery logging
- Health check command: `rock-paper-sync health`

---

### Milestone 7: Layer Architecture
**Goal**: Sophisticated layer management enabling OCR workflows and user layer preservation

#### 7.1 Multi-Layer Infrastructure
**Objective**: Extend the current single-layer system to support multiple layers with visibility semantics.

**Layer Model**:
- User-created layers MUST be preserved (never destroyed)
- System layers are added for specific purposes:
  - Content layer (generated text)
  - Annotation layer (fresh, top of stack, for new user annotations)
  - OCR-original layer (hidden, preserves original strokes)
  - Preservation layer (hidden, content we cannot handle)

**Deliverables**:
- Layer registry within DocumentModel tracking layer hierarchy
- Per-annotation layer assignment (extracted from .rm file)
- Layer metadata in annotation storage for round-trip preservation
- User layer detection and preservation during sync

**Technical Approach**:
- Extend `SceneTreeBlock` handling to track layer structure
- Add `layer_id` field to `Stroke` and `HighlightPlacement` domain types
- Implement layer inheritance for new annotations (default to annotation layer)
- Never delete or modify user-created layers

**Dependencies**: Milestone 5 (Bidirectional Sync)

#### 7.2 System Layer Types
**Objective**: Define system layer types for internal processing.

| Layer Type | Visibility | Purpose |
|------------|------------|---------|
| `content` | Visible | Generated text rendering |
| `annotations` | Visible | Fresh layer for new user annotations (top of stack) |
| `ocr-original` | Hidden | Original strokes preserved before OCR |
| `preservation` | Hidden | Content we cannot process |
| `user-*` | As-is | User-created layers (preserved exactly) |

**Deliverables**:
- Layer type enum with semantic definitions
- Auto-classification of new system layers
- Layer visibility toggling in generated .rm files
- User layer passthrough (no modification)

#### 7.3 Layer-Aware Generation
**Objective**: Modify the generator to output multi-layer .rm files with correct scene graph structure.

**Deliverables**:
- Multi-layer scene graph generation in `generator.py`
- Layer ordering: content → user layers → system layers → annotations (top)
- Layer visibility flags in output
- User layer preservation during regeneration

---

### Milestone 8: Advanced OCR Integration
**Goal**: In-place OCR for margin notes and inline text additions with proper layer separation

#### 8.1 Margin Note Detection and OCR
**Objective**: Automatically detect margin annotations, OCR them, and represent as Markdown footnotes.

**Detection Criteria**:
- Strokes with X position outside main text area (< -375px or > 375px from center)
- Clustered strokes forming coherent writing groups
- All strokes classified as writing (drawings out of scope)

**Processing Pipeline**:
```
[Margin Strokes] → [Cluster Detection] → [OCR Processing (TrOCR)]
       ↓                   ↓                       ↓
   Spatial filter    KDTree proximity         Text + confidence
                           ↓
              [Footnote Generation]
                           ↓
              [Layer Organization]
                     ↓           ↓
            ocr-original    content update
             (hidden)       (footnote text)
```

**Deliverables**:
- Margin detection algorithm based on text area bounds
- Footnote anchor point determination (nearest paragraph)
- Markdown footnote syntax generation:
  ```markdown
  This is the main paragraph text.[^1]

  [^1]: OCR'd margin note content here
  ```
- Original strokes preserved on hidden `ocr-original` layer
- Confidence-based fallback (low confidence → preserve strokes only)

**Configuration Options**:
```toml
[ocr]
margin_notes = true
margin_threshold = 50  # pixels outside text area
min_confidence = 0.7   # below this, keep strokes only
```

#### 8.2 Inline Text Addition OCR
**Objective**: Detect and OCR text added within or between existing paragraphs.

**Detection Scenarios**:

1. **Interline additions**: Writing between existing lines
   - Detect by Y-position not aligned with any generated text line
   - Associate with preceding or following paragraph based on proximity

2. **Paragraph insertions**: New paragraphs written in blank space
   - Detect by coherent writing clusters in whitespace areas
   - May span multiple "lines" of handwriting

**Deliverables**:
- Interline detection algorithm (Y-position gap analysis)
- Paragraph insertion detection (coherent writing in whitespace)
- Insertion point determination (before/after/within paragraph)
- Inline content integration (becomes part of paragraph content)

#### 8.3 OCR Layer Management
**Objective**: Implement the hidden original / visible content dual-layer pattern.

**Layer Workflow**:
1. Extract strokes from device
2. For OCR candidates (margin notes, inline additions):
   - Preserve original strokes on `ocr-original` layer (hidden)
   - Run OCR processing
   - Integrate OCR'd text into content (footnotes or inline)
3. Original handwriting preserved but hidden

**Deliverables**:
- Dual-layer generation for OCR'd content
- Layer linking metadata (original ↔ OCR'd relationship)
- Markdown representation with OCR markers:
  ```markdown
  <!-- OCR: id=abc123, confidence=0.92, layer=ocr-original -->
  [^margin-1]: This is the OCR'd text
  ```

#### 8.4 OCR Corrections and Learning
**Objective**: Enable user corrections to improve OCR over time.

**Correction Workflow**:
1. User edits footnote text in Obsidian
2. Sync detects change to OCR-generated content
3. System records correction pair: (original_image, corrected_text)
4. Corrections accumulate for fine-tuning

**Deliverables**:
- OCR correction detection (diff OCR markers before/after edit)
- Correction storage with image-text pairs
- Fine-tuning trigger (N corrections accumulated)
- Correction confidence boost (user corrections = high confidence)

**Dependencies**: Milestone 7 (Layer Architecture)

---

### Milestone 9: Advanced Content Support
**Goal**: Expand content types beyond plain text

#### 9.1 Table Rendering
**Objective**: Render Markdown tables as reMarkable text layouts.

**Approach**:
- Parse table structure from Markdown
- Calculate column widths based on content
- Render as aligned text blocks
- Preserve table annotations (future)

**Deliverables**:
- Table detection in parser
- Column layout algorithm
- Text-based table rendering
- Annotation anchoring to table cells

#### 9.2 Image Embedding
**Objective**: Include images from Obsidian in reMarkable documents.

**Approach**:
- Extract image references from Markdown
- Convert images to reMarkable-compatible format
- Embed as image layers in .rm files
- Handle image annotations (future)

**Deliverables**:
- Image extraction from Markdown
- Image format conversion (PNG/JPEG → reMarkable format)
- Image layer generation
- Image positioning within document flow

#### 9.3 LaTeX/Math Rendering
**Objective**: Render mathematical expressions as images.

**Approach**:
- Detect LaTeX blocks in Markdown
- Render via MathJax or KaTeX
- Convert to images
- Embed in document

**Deliverables**:
- LaTeX block detection
- Math rendering pipeline
- Image embedding for equations
- Inline vs. block equation handling

---

### Milestone 10: Ecosystem Integration
**Goal**: Deep integration with Obsidian and reMarkable ecosystems

#### 10.1 Obsidian Plugin
**Objective**: Native Obsidian integration for seamless UX.

**Features**:
- Sync status in status bar
- Sync commands in command palette
- Settings UI in Obsidian settings
- Conflict resolution UI
- Annotation preview

**Deliverables**:
- Obsidian plugin package
- IPC communication with sync daemon
- Settings synchronization
- Command registration

#### 10.2 Multi-Device Sync
**Objective**: Sync across multiple reMarkable devices.

**Approach**:
- Device registry management
- Per-device sync state
- Cross-device conflict resolution

**Deliverables**:
- Multi-device registration
- Device-specific sync state
- Cross-device annotation merging

---

## Milestone Dependencies

```
Milestone 4 (Complete)
     │
     ├──► Milestone 5: Bidirectional Sync
     │         │
     │         └──► Milestone 6: Zero-UI Experience
     │                   │
     │                   └──► Milestone 7: Layer Architecture
     │                             │
     │                             └──► Milestone 8: Advanced OCR
     │
     ├──► Milestone 9: Advanced Content (parallel track)
     │
     └──► Milestone 10: Ecosystem Integration (after M6)
```

---

## Success Criteria by Milestone

### Milestone 5: Bidirectional Sync
- [ ] Annotations created on device appear in Markdown
- [ ] Conflicts detected and resolved automatically (>95%)
- [ ] Pull sync completes in <30s for typical vault
- [ ] No data loss in any sync scenario

### Milestone 6: Zero-UI Experience
- [ ] Daemon runs reliably for weeks without intervention
- [ ] Syncs within 5s of file save
- [ ] Conflicts handled without user interaction (>99%)
- [ ] Clear status indication without popups

### Milestone 7: Layer Architecture
- [ ] Multi-layer .rm file generation validated on device
- [ ] Layer visibility toggling works in xochitl
- [ ] User-created layers preserved across sync cycles
- [ ] System layers correctly created (content, annotations, ocr-original, preservation)

### Milestone 8: Advanced OCR
- [ ] Margin notes detected with >90% precision
- [ ] OCR'd margin notes appear as footnotes in Markdown
- [ ] Original strokes preserved on hidden layer
- [ ] Inline text additions OCR'd and inserted correctly
- [ ] User corrections improve OCR accuracy over time

### Milestone 9: Advanced Content
- [ ] Tables render correctly on device
- [ ] Images display in documents
- [ ] Math expressions render as images

### Milestone 10: Ecosystem Integration
- [ ] Obsidian plugin available in community plugins
- [ ] Multi-device sync without conflicts

---

## Risk Register

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| reMarkable API changes | High | Medium | Protocol monitoring, version detection |
| rmscene library limitations | Medium | Low | Contribute upstream, fork if needed |
| OCR accuracy for cursive | Medium | High | Fine-tuning pipeline, confidence thresholds |
| Sync conflicts at scale | High | Medium | Extensive testing, conflict logging |
| Performance with large vaults | Medium | Medium | Incremental sync, parallel processing |
| Platform-specific daemon issues | Low | Medium | Cross-platform testing, fallback modes |

---

## Open Questions

### To Be Designed (Milestone 5)

1. **Conflict Resolution Strategy**: What should the default conflict resolution approach be?
   - Auto-merge with best effort?
   - Always prompt user?
   - Configurable per-vault?
   - *Note: To be designed as part of Milestone 5 implementation.*

### To Be Designed (Milestone 10)

2. **Obsidian Plugin Scope**: Minimal (status only) or full-featured (settings, preview, conflict UI)?
   - *Note: To be designed as part of Milestone 10 implementation.*

### Resolved Decisions

| Decision | Resolution |
|----------|------------|
| Margin note representation | Footnotes (opinionated) |
| Drawings classification | Out of scope; all strokes classified as writing |
| Sync battery trade-offs | N/A; cloud-native sync has no device battery impact |
| Multi-vault sync order | N/A; 1:1 document mapping makes order irrelevant |
| Template library | Out of scope |
| Layer UX | Layers are for preservation, not user organization |

---

## Appendix: Technical Architecture Notes

### Layer Implementation Details

The reMarkable .rm file format uses a scene graph structure where each layer is represented by a `SceneTreeBlock` with child nodes for content. Current implementation uses a single layer (CrdtId 0:11) for all content.

Multi-layer support requires:
1. Extending `CrdtIdCounter` to manage multiple layer IDs
2. Modifying `SceneTreeBlockWriter` to output multiple trees
3. Adding layer metadata to `RootDocumentBlock`
4. Updating scene adapter to route annotations to correct layers

### OCR Integration Architecture

Current OCR pipeline:
```
Strokes → Image Generation → TrOCR API → Text + Confidence
```

Enhanced pipeline for layer support:
```
Strokes → Spatial Classification → [Margin? → Footnote OCR]
                                 → [Inline? → Paragraph OCR]
              ↓
    [OCR'd Text] + [Original Stroke Layer Assignment]
              ↓
    [Dual Layer Generation: ocr-original (hidden) + content update]
```

Note: All strokes are classified as writing. Drawing classification is out of scope.

### Bidirectional Sync Protocol

Pull sync flow:
```
1. GET /documents (list all)
2. Compare with local state (hash-based)
3. For changed documents:
   a. GET /documents/{id}/files (file list)
   b. GET /files/{hash} (download .rm files)
   c. Extract annotations
   d. Merge with local Markdown
   e. Update state database
```

Push sync flow (existing):
```
1. Detect local file changes
2. Parse Markdown → ContentBlocks
3. Merge with existing annotations
4. Generate .rm files
5. Upload via Sync v3 protocol
6. Update state database
```

### Daemon Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Sync Daemon                          │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │ File Watcher│  │ Device      │  │ Scheduler       │  │
│  │ (inotify)   │  │ Monitor     │  │ (intervals)     │  │
│  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘  │
│         │                │                  │           │
│         └────────────────┼──────────────────┘           │
│                          ▼                              │
│                 ┌────────────────┐                      │
│                 │  Event Queue   │                      │
│                 └────────┬───────┘                      │
│                          ▼                              │
│                 ┌────────────────┐                      │
│                 │ Sync Scheduler │                      │
│                 │ (debounce,     │                      │
│                 │  batch, rate)  │                      │
│                 └────────┬───────┘                      │
│                          ▼                              │
│                 ┌────────────────┐                      │
│                 │  Sync Engine   │                      │
│                 └────────┬───────┘                      │
│                          ▼                              │
│                 ┌────────────────┐                      │
│                 │ Status Reporter│                      │
│                 └────────────────┘                      │
└─────────────────────────────────────────────────────────┘
```

---

*This roadmap is a living document. Updates will be made as product decisions are finalized and technical discoveries are made during implementation.*
