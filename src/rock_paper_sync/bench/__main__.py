"""Bench CLI entry point.

Usage:
    uv run python -m rock_paper_sync.bench render doc.md [-o outdir] [--overlay]
    uv run python -m rock_paper_sync.bench diff a.png b.png [-o out.png]
    uv run python -m rock_paper_sync.bench preview <vault_dir> <doc.md> [-o outdir]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rock_paper_sync.bench.diffing import diff_files
from rock_paper_sync.bench.rendering import render_markdown


def cmd_render(args: argparse.Namespace) -> int:
    md_path = Path(args.doc)
    if not md_path.is_file():
        print(f"error: no such file: {md_path}", file=sys.stderr)
        return 2
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    result = render_markdown(md_path, overlay=args.overlay)
    for page in result.pages:
        out_path = outdir / f"{md_path.stem}_page{page.index + 1:02d}.png"
        page.image.save(out_path, "PNG")
        print(f"page {page.index + 1}: {page.line_count} lines -> {out_path}")
    print(f"{len(result.pages)} page(s) rendered in {result.elapsed_seconds:.2f}s")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    path_a, path_b = Path(args.a), Path(args.b)
    for p in (path_a, path_b):
        if not p.is_file():
            print(f"error: no such file: {p}", file=sys.stderr)
            return 2
    out_path = Path(args.out) if args.out else None
    result = diff_files(path_a, path_b, out_path)
    print(f"pHash distance: {result.phash_distance}")
    print(f"changed pixels: {result.changed_pixels}")
    if out_path is not None:
        print(f"heat overlay -> {out_path}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    vault_dir = Path(args.vault_dir)
    md_path = vault_dir / args.doc
    if not md_path.is_file():
        print(f"error: no such file: {md_path}", file=sys.stderr)
        return 2
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # "After" state: what the production parse->generate pipeline would push
    # for the CURRENT markdown.
    result = render_markdown(md_path, overlay=args.overlay)
    for page in result.pages:
        out_path = outdir / f"after_page{page.index + 1:02d}.png"
        page.image.save(out_path, "PNG")
        print(f"after page {page.index + 1}: {page.line_count} lines -> {out_path}")
    print(f"{len(result.pages)} page(s) rendered in {result.elapsed_seconds:.2f}s")

    # "Before" state: the offline pull/reanchor half (PullSyncEngine) needs
    # the sync state DB, cached device .rm files, and change detection wiring.
    # Not implemented yet -- say so loudly instead of faking it.
    print(
        "note: offline reanchor preview (before-state via PullSyncEngine) is "
        "NOT implemented yet; only the current-markdown render is shown. "
        "See docs/DEVICE_REPLICA_DESIGN.md Layer C."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rock_paper_sync.bench",
        description="Layout testbench: render, diff, and preview documents locally.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render", help="Render a markdown doc to one PNG per page")
    p_render.add_argument("doc", help="Path to markdown file")
    p_render.add_argument("-o", "--outdir", default="bench_out", help="Output directory")
    p_render.add_argument(
        "--overlay",
        action="store_true",
        help="Draw line grid, text bounds, wrap points, page break, anchor markers",
    )
    p_render.set_defaults(func=cmd_render)

    p_diff = sub.add_parser("diff", help="Diff two PNGs: heat overlay + pHash distance")
    p_diff.add_argument("a", help="First PNG")
    p_diff.add_argument("b", help="Second PNG")
    p_diff.add_argument("-o", "--out", default=None, help="Write heat overlay PNG here")
    p_diff.set_defaults(func=cmd_diff)

    p_prev = sub.add_parser("preview", help="Offline sync preview for a vault document")
    p_prev.add_argument("vault_dir", help="Vault directory")
    p_prev.add_argument("doc", help="Markdown file path relative to the vault")
    p_prev.add_argument("-o", "--outdir", default="bench_preview", help="Output directory")
    p_prev.add_argument("--overlay", action="store_true", help="Draw layout overlay")
    p_prev.set_defaults(func=cmd_preview)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
