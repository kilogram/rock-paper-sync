# Font Size Calibration

This document helps discover the device's actual font size by measuring known character widths.

## Methodology

Highlight the entire string of m's in each test. We'll extract the highlight width and compare to theoretical predictions at different point sizes.

At 226 DPI with Noto Sans Regular, character 'm' has these widths:
- 8pt: m ≈ 12.4px → 25 m's ≈ 309px
- 10pt: m ≈ 17.7px → 25 m's ≈ 442px
- 12pt: m ≈ 21.2px → 25 m's ≈ 531px
- 14pt: m ≈ 24.8px → 25 m's ≈ 619px

## Test String (25 m's)

mmmmmmmmmmmmmmmmmmmmmmmmm

## Additional Test: Wide Characters (20 W's)

WWWWWWWWWWWWWWWWWWWW

Expected widths at 226 DPI:
- 8pt: W ≈ 15.0px → 20 W's ≈ 300px
- 10pt: W ≈ 21.5px → 20 W's ≈ 430px
- 12pt: W ≈ 25.8px → 20 W's ≈ 516px
- 14pt: W ≈ 30.0px → 20 W's ≈ 600px

## Narrow Test (20 i's)

iiiiiiiiiiiiiiiiiiii

Expected widths at 226 DPI:
- 8pt: i ≈ 4.4px → 20 i's ≈ 88px
- 10pt: i ≈ 6.3px → 20 i's ≈ 126px
- 12pt: i ≈ 7.6px → 20 i's ≈ 152px
- 14pt: i ≈ 8.8px → 20 i's ≈ 176px

## Space Width Test (20 spaces between pipes)

|                    |

Expected widths at 226 DPI (between pipes):
- 8pt: space ≈ 3.9px → 20 spaces ≈ 78px
- 10pt: space ≈ 5.6px → 20 spaces ≈ 112px
- 12pt: space ≈ 6.7px → 20 spaces ≈ 134px
- 14pt: space ≈ 7.8px → 20 spaces ≈ 156px
