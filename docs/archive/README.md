# Historical Documentation Archive

This directory contains historical design documents, calibration results, and refactoring notes that are no longer current but preserved for reference.

## Contents

### Coordinate System Evolution

- **COORDINATE_CALIBRATION_RESULTS.md** - Device calibration measurements (2025-11-30)
- **COORDINATE_SYSTEMS.md** - Early coordinate system analysis
- **COORDINATE_SYSTEM_SUMMARY.md** - Summary of coordinate findings
- **DPI_FIX_IMPACT_ANALYSIS.md** - Analysis of DPI-related fixes

**Current documentation**: See `../RENDERER_COORDINATE_MODEL.md` for the production coordinate system.

### Layout Refactoring History

- **FONT_LAYOUT_REFACTORING.md** - First layout engine refactoring
- **FONT_LAYOUT_REFACTORING_V2.md** - Second iteration of layout refactoring
- **FONT_SIZE_DISCOVERY.md** - Font size calibration notes

**Current documentation**: The layout engine is now stable and documented in code docstrings.

## Why Archived?

These documents were valuable during development but have been superseded by:

1. **Production code** - The implemented solutions are now the source of truth
2. **RMSCENE_FINDINGS.md** - Consolidated technical findings about the reMarkable format
3. **Inline documentation** - Code docstrings provide implementation details
4. **RENDERER_COORDINATE_MODEL.md** - Current coordinate system documentation

## When to Reference

These documents are useful for:

- Understanding the evolution of the coordinate system implementation
- Historical context on why certain design decisions were made
- Debugging coordinate-related issues by tracing the calibration process
- Learning about the discovery process for reverse-engineering the reMarkable format
