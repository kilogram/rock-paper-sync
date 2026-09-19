# Geometry Calibration

This document has ruler strokes at known physical positions.

## Coordinate System

- Document canvas: 1404 × 1872 doc pixels (reMarkable 2 format)
- Document DPI: 226 (reMarkable 2's resolution)
- Physical device: Paper Pro Move
  - Screen: 954 × 1696 pixels at 264 PPI
  - Scale: 1.168× (264/226)
- Viewport: ~1443 doc pixels visible (after UI chrome)

## Ruler Reference

The strokes in the .rm file show:
- Border at visible viewport edges
- 71.8mm vertical ruler with 1cm tick marks
- Calibrated using empirical measurements with physical ruler

## Coordinate System Discovery

Document coordinates use reMarkable 2's 226 DPI regardless of target device.
When rendered on Paper Pro Move (264 PPI), coordinates are scaled
1.168× to match the physical screen.

Content below y≈1443 is off-screen but exists in the document.
