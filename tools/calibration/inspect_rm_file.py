#!/usr/bin/env python3
"""Inspect .rm file structure and paper_size."""

import sys
from pathlib import Path

import rmscene


def inspect_rm_file(rm_path: Path):
    """Inspect an .rm file and print its structure."""
    print(f"Inspecting: {rm_path}")
    print("=" * 60)

    with open(rm_path, 'rb') as f:
        blocks = list(rmscene.read_blocks(f))

    print(f"Total blocks: {len(blocks)}\n")

    for i, block in enumerate(blocks):
        block_type = type(block).__name__
        print(f"Block {i}: {block_type}")

        if isinstance(block, rmscene.scene_stream.SceneInfo):
            print(f"  - current_layer: {block.current_layer}")
            print(f"  - background_visible: {block.background_visible}")
            print(f"  - root_document_visible: {block.root_document_visible}")
            print(f"  - paper_size: {block.paper_size}")
            if block.paper_size:
                print(f"    → Width: {block.paper_size[0]} pixels")
                print(f"    → Height: {block.paper_size[1]} pixels")

        elif isinstance(block, rmscene.scene_stream.PageInfoBlock):
            print(f"  - loads_count: {block.loads_count}")
            print(f"  - merges_count: {block.merges_count}")
            print(f"  - text_chars_count: {block.text_chars_count}")
            print(f"  - text_lines_count: {block.text_lines_count}")

        elif isinstance(block, rmscene.scene_stream.RootTextBlock):
            text = block.value
            print(f"  - pos_x: {text.pos_x}")
            print(f"  - pos_y: {text.pos_y}")
            print(f"  - width: {text.width}")

        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python tools/calibration/inspect_rm_file.py <file.rm>")
        sys.exit(1)

    rm_file = Path(sys.argv[1])
    if not rm_file.exists():
        print(f"Error: File not found: {rm_file}")
        sys.exit(1)

    inspect_rm_file(rm_file)
