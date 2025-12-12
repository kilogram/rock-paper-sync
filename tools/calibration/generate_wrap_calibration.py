#!/usr/bin/env python3
"""Generate word wrap calibration document targeting specific pixel widths.

This tool creates a calibration document with lines designed to test
exact wrap boundaries by targeting specific pixel widths using font metrics.

Pattern for each test line:
    e [filler chars to target width] x

Where 'x' should appear on a new line if the target width exceeds wrap boundary.

Usage:
    # Generate with default settings (Paper Pro Move):
    uv run python tools/calibration/generate_wrap_calibration.py

    # Generate targeting specific widths:
    uv run python tools/calibration/generate_wrap_calibration.py --min-width 740 --max-width 770

    # Output to specific file:
    uv run python tools/calibration/generate_wrap_calibration.py -o tests/record_replay/fixtures/calibration_wrap.md
"""

from __future__ import annotations

import argparse
from pathlib import Path


def get_char_width(char: str, font_size: float = 29.5) -> float:
    """Get width of a character using font metrics."""
    try:
        from rock_paper_sync.font_metrics import char_width
        return char_width(char, font_size)
    except ImportError:
        # Fallback approximations if font_metrics not available
        approximations = {
            'i': 5.5,
            'l': 5.5,
            'm': 25.0,
            'w': 23.0,
            'e': 15.0,
            'x': 14.0,
            'a': 15.0,
            ' ': 7.0,
        }
        return approximations.get(char, 15.0)


def get_text_width(text: str, font_size: float = 29.5) -> float:
    """Get width of text string using font metrics."""
    try:
        from rock_paper_sync.font_metrics import text_width
        return text_width(text, font_size)
    except ImportError:
        return sum(get_char_width(c, font_size) for c in text)


def generate_line_to_width(
    target_width: float,
    filler_char: str = 'a',
    font_size: float = 29.5,
) -> tuple[str, float]:
    """Generate a line targeting a specific pixel width.

    Returns:
        Tuple of (line_text, actual_width)
    """
    # Start with marker "e "
    line = "e "
    current_width = get_text_width(line, font_size)

    # Add filler characters until we approach target
    filler_width = get_char_width(filler_char, font_size)

    while current_width + filler_width < target_width - get_char_width(' ', font_size) - get_char_width('x', font_size):
        line += filler_char
        current_width += filler_width

    # Add space and wrap marker
    line += " x"
    final_width = get_text_width(line, font_size)

    return line, final_width


def generate_mixed_line_to_width(
    target_width: float,
    font_size: float = 29.5,
) -> tuple[str, float]:
    """Generate a line with mixed character widths targeting a specific pixel width.

    Uses a mix of narrow (i), average (e,a), and wide (m) characters
    to achieve more precise width targeting.

    Returns:
        Tuple of (line_text, actual_width)
    """
    # Start with marker "e "
    line = "e "
    current_width = get_text_width(line, font_size)

    # Target width minus space and 'x' marker
    space_width = get_char_width(' ', font_size)
    x_width = get_char_width('x', font_size)
    fill_target = target_width - space_width - x_width

    # Character options sorted by width (narrow to wide)
    chars = [
        ('i', get_char_width('i', font_size)),
        ('n', get_char_width('n', font_size)),
        ('o', get_char_width('o', font_size)),
        ('m', get_char_width('m', font_size)),
    ]

    # Greedy fill: use widest char that fits, then progressively narrower
    while current_width < fill_target:
        added = False
        # Try chars from widest to narrowest
        for char, width in reversed(chars):
            if current_width + width <= fill_target:
                line += char
                current_width += width
                added = True
                break

        if not added:
            # Can't fit any more chars
            break

    # Add space and wrap marker
    line += " x"
    final_width = get_text_width(line, font_size)

    return line, final_width


def generate_calibration_document(
    layout_width: float = 758.0,
    font_size: float = 29.5,
    width_steps: list[float] | None = None,
) -> str:
    """Generate the complete calibration document.

    Args:
        layout_width: Expected wrap width in pixels (default: 758 for Paper Pro Move)
        font_size: Font size in points
        width_steps: List of target widths to test (default: range around layout_width)

    Returns:
        Markdown document content
    """
    if width_steps is None:
        # Test widths from -20 to +20 around layout width in 2px steps
        width_steps = [layout_width + offset for offset in range(-20, 22, 2)]

    lines = [
        "# Word Wrap Calibration",
        "",
        "Highlight the 'e' at the START of each line.",
        "The 'x' at the end should wrap to a new line if width exceeds wrap boundary.",
        "",
        f"Target layout width: {layout_width}px",
        f"Font size: {font_size}pt",
        "",
    ]

    # Generate unique lines (deduplicate since char widths are discrete)
    seen_lines = set()
    for target in width_steps:
        line, actual = generate_precise_line(target, font_size)
        if line not in seen_lines:
            seen_lines.add(line)
            lines.append(f"{line}")
            lines.append("")

    return "\n".join(lines)


def generate_precise_line(
    target_width: float,
    font_size: float = 29.5,
) -> tuple[str, float]:
    """Generate a line hitting precise pixel width using mixed chars.

    Uses 'm' for coarse filling, then 'i' for fine-tuning.

    Returns:
        Tuple of (line_text, actual_width)
    """
    # Start with marker "e "
    line = "e "
    current_width = get_text_width(line, font_size)

    # Reserve space for " x" at end
    space_width = get_char_width(' ', font_size)
    x_width = get_char_width('x', font_size)
    fill_target = target_width - space_width - x_width

    # Coarse fill with 'm'
    m_width = get_char_width('m', font_size)
    while current_width + m_width <= fill_target:
        line += 'm'
        current_width += m_width

    # Fine-tune with 'i' (narrow char)
    i_width = get_char_width('i', font_size)
    while current_width + i_width <= fill_target:
        line += 'i'
        current_width += i_width

    # Add space and wrap marker
    line += " x"
    final_width = get_text_width(line, font_size)

    return line, final_width


def main():
    parser = argparse.ArgumentParser(
        description="Generate word wrap calibration document"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path(__file__).parent.parent.parent / "tests" / "record_replay" / "fixtures" / "calibration_wrap.md",
        help="Output file path",
    )
    parser.add_argument(
        "--layout-width",
        type=float,
        default=758.0,
        help="Layout width in pixels (default: 758 for Paper Pro Move)",
    )
    parser.add_argument(
        "--font-size",
        type=float,
        default=29.5,
        help="Font size in points (default: 29.5)",
    )
    parser.add_argument(
        "--min-width",
        type=float,
        default=None,
        help="Minimum target width (default: layout_width - 20)",
    )
    parser.add_argument(
        "--max-width",
        type=float,
        default=None,
        help="Maximum target width (default: layout_width + 20)",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=5.0,
        help="Width step size (default: 5px)",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print to stdout instead of file",
    )

    args = parser.parse_args()

    # Calculate width steps
    min_w = args.min_width if args.min_width else args.layout_width - 20
    max_w = args.max_width if args.max_width else args.layout_width + 20
    width_steps = []
    w = min_w
    while w <= max_w:
        width_steps.append(w)
        w += args.step

    # Generate document
    content = generate_calibration_document(
        layout_width=args.layout_width,
        font_size=args.font_size,
        width_steps=width_steps,
    )

    if args.print:
        print(content)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
        print(f"Generated: {args.output}")
        print(f"Width range: {min_w}px - {max_w}px (step: {args.step}px)")


if __name__ == "__main__":
    main()
