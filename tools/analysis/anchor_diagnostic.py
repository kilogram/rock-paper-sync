"""Diagnostic tool for TreeNodeBlock anchor analysis.

Compares anchor values between Phase 2 (source) and Phase 3 Golden (target).

Usage:
    uv run python tools/analysis/anchor_diagnostic.py
"""

import rmscene
from pathlib import Path

TESTDATA = Path("tests/record_replay/testdata/multi_trip/phases")


def analyze_rm(rm_path: Path) -> dict:
    """Extract TreeNodeBlock and stroke info from .rm file."""
    with open(rm_path, "rb") as f:
        blocks = list(rmscene.read_blocks(f))

    result = {"strokes": 0, "tree_nodes": [], "stroke_parents": []}

    for b in blocks:
        bt = type(b).__name__

        if bt == "TreeNodeBlock" and hasattr(b, "group") and b.group:
            g = b.group
            node_id = g.node_id
            anchor = g.anchor_id.value if g.anchor_id else None
            if node_id and node_id.part1 == 2:  # User annotations only
                result["tree_nodes"].append(
                    {
                        "node_id": (node_id.part1, node_id.part2),
                        "anchor": (anchor.part1, anchor.part2) if anchor else None,
                    }
                )

        elif "Line" in bt:
            result["strokes"] += 1
            parent_id = getattr(b, "parent_id", None)
            if parent_id:
                result["stroke_parents"].append((parent_id.part1, parent_id.part2))

    return result


def main():
    print("=" * 70)
    print("ANCHOR DIAGNOSTIC: Phase 2 vs Phase 3 Golden")
    print("=" * 70)

    # Phase 2 - source annotations
    print("\n--- PHASE 2 (Source - strokes on page 0) ---")
    phase2_dir = TESTDATA / "phase_2_phase_2" / "rm_files"
    for idx, rm_file in enumerate(sorted(phase2_dir.glob("*.rm"))):
        info = analyze_rm(rm_file)
        if info["strokes"] > 0:
            print(f"\nPage {idx}: {info['strokes']} strokes")
            print(f"  TreeNodes:")
            for tn in info["tree_nodes"]:
                print(f"    node_id={tn['node_id']}, anchor={tn['anchor']}")
            print(f"  Stroke parents: {info['stroke_parents'][:3]}...")

    # Phase 3 Golden - what works on device
    print("\n--- PHASE 3 GOLDEN (Target - strokes on page 1) ---")
    golden_dir = TESTDATA / "phase_3_golden_native" / "rm_files"
    for idx, rm_file in enumerate(sorted(golden_dir.glob("*.rm"))):
        info = analyze_rm(rm_file)
        if info["strokes"] > 0:
            print(f"\nPage {idx}: {info['strokes']} strokes")
            print(f"  TreeNodes:")
            for tn in info["tree_nodes"]:
                print(f"    node_id={tn['node_id']}, anchor={tn['anchor']}")
            print(f"  Stroke parents: {info['stroke_parents'][:3]}...")

    # Key comparison
    print("\n" + "=" * 70)
    print("KEY INSIGHT:")
    print("  Phase 2 anchors: ~630+ (document-level offsets)")
    print("  Golden anchors:  ~117-121 (page-local offsets)")
    print("  Our reanchoring must produce ~117-121, not ~630+")
    print("=" * 70)


if __name__ == "__main__":
    main()
