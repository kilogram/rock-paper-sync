# Documentation Index

Complete guide to rock-paper-sync documentation organized by audience and purpose.

## Quick Start

- **[README.md](README.md)** - User documentation, installation, and usage guide
- **[CLAUDE.md](CLAUDE.md)** - Development guide for contributors

## User Documentation

### Setup and Configuration
- **[README.md](README.md)** - Installation, configuration, basic usage
- **[docs/MULTI_VAULT.md](docs/MULTI_VAULT.md)** - Multi-vault configuration guide

### Features
- **Annotation Preservation** - Highlights and handwritten notes preserved across markdown updates
  - See [src/rock_paper_sync/annotations/README.md](src/rock_paper_sync/annotations/README.md) for architecture overview
- **OCR Integration** - Optional handwriting recognition
  - See [docs/OCR_SYSTEM.md](docs/OCR_SYSTEM.md) for setup and configuration

## Developer Documentation

### Architecture
- **[src/rock_paper_sync/annotations/README.md](src/rock_paper_sync/annotations/README.md)** - Annotation system architecture
  - Handler protocol, coordinate transformation, merge logic
- **[docs/ANNOTATION_ARCHITECTURE_V2.md](docs/ANNOTATION_ARCHITECTURE_V2.md)** - AnchorContext design document
  - Content-based anchoring, fuzzy matching, cross-page migration

### Technical Deep Dives

#### reMarkable Format
- **[docs/SYNC_PROTOCOL.md](docs/SYNC_PROTOCOL.md)** - Sync v3 protocol (reverse engineered)
  - hashOfHashesV3 algorithm, CRDT format, required files
- **[docs/RMSCENE_FINDINGS.md](docs/RMSCENE_FINDINGS.md)** - rmscene library integration
  - Block structure, text positioning, calibration history

#### Coordinate Systems
- **[docs/STROKE_ANCHORING.md](docs/STROKE_ANCHORING.md)** - Stroke anchoring to text
  - TreeNodeBlock structure, anchor drift, proportional routing
- **[docs/RENDERER_COORDINATE_MODEL.md](docs/RENDERER_COORDINATE_MODEL.md)** - Coordinate transformations for rendering
  - Text positioning, highlight rendering, stroke baseline offset
- **[src/rock_paper_sync/annotations/docs/STROKES.md](src/rock_paper_sync/annotations/docs/STROKES.md)** - Stroke coordinate transformation
  - Dual-anchor system, clustering, OCR integration
- **[src/rock_paper_sync/annotations/docs/HIGHLIGHTS.md](src/rock_paper_sync/annotations/docs/HIGHLIGHTS.md)** - Highlight handling
  - Text-based matching, simple coordinate transformation

#### OCR System
- **[docs/OCR_SYSTEM.md](docs/OCR_SYSTEM.md)** - OCR architecture and deployment
  - Service protocol, Runpods integration, training pipeline
  - Paragraph mapping, correction tracking, fine-tuning

### Testing

- **[docs/RECORD_REPLAY_FRAMEWORK.md](docs/RECORD_REPLAY_FRAMEWORK.md)** - Device testing framework
  - Online/offline modes, test harness, OCR mocking
- **[tests/README.md](tests/README.md)** - Test suite overview
- **[tests/record_replay/README.md](tests/record_replay/README.md)** - Record/replay test examples

### Development

- **[CLAUDE.md](CLAUDE.md)** - Development workflow and guidelines
  - `uv run` commands, git workflow, testing, code style
- **[docs/TODO.md](docs/TODO.md)** - Technical debt and deferred items
  - Known issues, future refactoring, coverage gaps

## Historical Documentation

- **[docs/archive/](docs/archive/)** - Historical design documents
  - Coordinate calibration results, layout refactoring notes
  - Preserved for reference but superseded by current implementation

## Documentation Map by Component

### Core Sync Pipeline
```
[Obsidian .md] → parser.py → generator.py → [.rm files] → rm_cloud_sync.py → [Device]
```
- **parser.py** - See docstrings and [README.md](README.md#how-it-works)
- **generator.py** - See docstrings and [docs/RMSCENE_FINDINGS.md](docs/RMSCENE_FINDINGS.md)
- **rm_cloud_sync.py** - See docstrings and [docs/SYNC_PROTOCOL.md](docs/SYNC_PROTOCOL.md)

### Annotation System
```
[Old .rm] → DocumentModel → AnnotationMerger → [New .rm with annotations]
```
- **Architecture** - [src/rock_paper_sync/annotations/README.md](src/rock_paper_sync/annotations/README.md)
- **Design** - [docs/ANNOTATION_ARCHITECTURE_V2.md](docs/ANNOTATION_ARCHITECTURE_V2.md)
- **Coordinates** - [docs/STROKE_ANCHORING.md](docs/STROKE_ANCHORING.md)
- **Handlers** - [src/rock_paper_sync/annotations/docs/](src/rock_paper_sync/annotations/docs/)

### OCR Pipeline
```
[.rm strokes] → clustering → rendering → OCR service → [markdown OCR blocks]
```
- **System** - [docs/OCR_SYSTEM.md](docs/OCR_SYSTEM.md)
- **Coordinates** - [src/rock_paper_sync/annotations/docs/STROKES.md](src/rock_paper_sync/annotations/docs/STROKES.md)

## Contributing

Before contributing, please read:
1. **[CLAUDE.md](CLAUDE.md)** - Development workflow and code style
2. **[docs/TODO.md](docs/TODO.md)** - Current priorities and known issues
3. **Component-specific docs** for the area you're working on

## Getting Help

1. Check code docstrings (most implementation details are in code)
2. Review relevant documentation from this index
3. Enable debug logging: see [CLAUDE.md](CLAUDE.md#troubleshooting)
4. Check [docs/TODO.md](docs/TODO.md) for known issues
