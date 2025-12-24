#!/usr/bin/env python3
"""Convert .rm files to PNG using rmc + cairosvg.

This script uses off-the-shelf tools to render .rm files, providing
an independent verification method for our coordinate transformation code.

Prerequisites:
    Install rmc via pipx (not uv, due to rmscene version conflict):
        pipx install rmc

Usage:
    uv run python tools/analysis/rm_to_png.py input.rm output.png
    uv run python tools/analysis/rm_to_png.py input.rm output.png --width 2808 --height 3744
    uv run python tools/analysis/rm_to_png.py input.rm  # outputs to input.png
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cairosvg


def check_rmc_installed() -> bool:
    """Check if rmc is available in PATH."""
    return shutil.which("rmc") is not None


def rm_to_svg(rm_file: Path, svg_file: Path) -> None:
    """Convert .rm file to SVG using rmc.

    Args:
        rm_file: Input .rm file path
        svg_file: Output .svg file path

    Raises:
        FileNotFoundError: If rmc is not installed
        subprocess.CalledProcessError: If rmc fails
    """
    if not check_rmc_installed():
        raise FileNotFoundError(
            "rmc not found. Install it with: pipx install rmc\n"
            "(Note: rmc requires rmscene <0.7, so it must be installed separately)"
        )

    result = subprocess.run(
        ["rmc", "-t", "svg", "-o", str(svg_file), str(rm_file)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, "rmc", output=result.stdout, stderr=result.stderr
        )


def svg_to_png(svg_file: Path, png_file: Path, width: int = 1404, height: int = 1872) -> None:
    """Convert SVG to PNG using cairosvg.

    Args:
        svg_file: Input SVG file path
        png_file: Output PNG file path
        width: Output width in pixels (default: reMarkable page width)
        height: Output height in pixels (default: reMarkable page height)
    """
    cairosvg.svg2png(
        url=str(svg_file),
        write_to=str(png_file),
        output_width=width,
        output_height=height,
    )


def rm_to_png(
    rm_file: Path,
    output: Path | None = None,
    width: int = 1404,
    height: int = 1872,
) -> Path:
    """Convert .rm file to PNG using rmc + cairosvg.

    Args:
        rm_file: Input .rm file path
        output: Output PNG path (default: same as input with .png extension)
        width: Output width in pixels
        height: Output height in pixels

    Returns:
        Path to output PNG file
    """
    if output is None:
        output = rm_file.with_suffix(".png")

    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        svg_path = Path(tmp.name)

    try:
        rm_to_svg(rm_file, svg_path)
        svg_to_png(svg_path, output, width, height)
    finally:
        svg_path.unlink(missing_ok=True)

    return output


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Convert .rm files to PNG using rmc + cairosvg",
        epilog="Requires rmc: pipx install rmc",
    )
    parser.add_argument("input", type=Path, help="Input .rm file")
    parser.add_argument("output", type=Path, nargs="?", help="Output PNG file (default: input.png)")
    parser.add_argument("--width", type=int, default=1404, help="Output width (default: 1404)")
    parser.add_argument("--height", type=int, default=1872, help="Output height (default: 1872)")

    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    try:
        output = rm_to_png(args.input, args.output, args.width, args.height)
        print(f"Created: {output}")
        return 0
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"Error: rmc failed: {e.stderr}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
