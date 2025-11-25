#!/usr/bin/env python3
"""Diagnostic script to validate clustering and paragraph mapping.

This script:
1. Loads testdata .rm files
2. Runs clustering algorithm (using production code)
3. Maps clusters to paragraphs
4. Renders cluster PNGs for visual inspection
5. Reports detailed results

Usage:
    uv run python tests/device_bench/diagnose_clustering.py
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

from rock_paper_sync.annotations import read_annotations, AnnotationType
from rock_paper_sync.annotation_mapper import extract_text_blocks_from_rm
from rock_paper_sync.parser import parse_content
from rock_paper_sync.config import OCRConfig
from rock_paper_sync.ocr.integration import OCRProcessor

# Setup logging - disable debug noise
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Silence rmscene warnings
logging.getLogger("rmscene.tagged_block_reader").setLevel(logging.ERROR)
logging.getLogger("rmscene.scene_stream").setLevel(logging.ERROR)
logging.getLogger("rock_paper_sync.parser").setLevel(logging.ERROR)
logging.getLogger("rock_paper_sync.ocr.paragraph_mapper").setLevel(logging.ERROR)

TESTDATA_DIR = Path(__file__).parent / "fixtures" / "testdata" / "ocr_handwriting"
OUTPUT_DIR = TESTDATA_DIR / "diagnostic_output"


def main():
    """Run diagnostic analysis on testdata."""
    print("=" * 80)
    print("OCR CLUSTERING DIAGNOSTIC")
    print("=" * 80)

    # Load manifest
    manifest_path = TESTDATA_DIR / "manifest.json"
    if not manifest_path.exists():
        print(f"❌ No manifest found at {manifest_path}")
        return

    manifest = json.loads(manifest_path.read_text())
    print(f"\n📄 Source: {manifest['source_document']}")
    print(f"📦 Files: {manifest['num_rm_files']} .rm files")

    # Load markdown
    markdown_path = TESTDATA_DIR / "markdown" / manifest["source_document"]
    if not markdown_path.exists():
        print(f"❌ Source markdown not found at {markdown_path}")
        return

    markdown_content = markdown_path.read_text()
    markdown_blocks = parse_content(markdown_content)
    print(f"📝 Parsed {len(markdown_blocks)} markdown blocks")

    # Create output directory
    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"📂 Output directory: {OUTPUT_DIR}")

    # Create OCR processor (with mock state manager)
    config = OCRConfig(enabled=True, cache_dir=OUTPUT_DIR)
    processor = OCRProcessor(config, MagicMock())

    # Analyze each .rm file
    total_clusters = 0
    for i, rm_filename in enumerate(manifest["rm_files"], 1):
        print(f"\n{'─' * 80}")
        print(f"FILE {i}: {rm_filename}")
        print(f"{'─' * 80}")

        rm_file = TESTDATA_DIR / rm_filename

        # Read annotations and text blocks
        try:
            annotations = read_annotations(rm_file)
            rm_text_blocks, text_origin_y = extract_text_blocks_from_rm(rm_file)
        except Exception as e:
            print(f"❌ Failed to read {rm_filename}: {e}")
            continue

        print(f"\n📍 Annotations: {len(annotations)}")
        print(f"📄 Text blocks: {len(rm_text_blocks)}")
        print(f"📏 Text origin Y: {text_origin_y}")

        if not annotations:
            print("⚠️  No annotations found")
            continue

        # Analyze annotation types
        strokes = [a for a in annotations if a.type == AnnotationType.STROKE]
        highlights = [a for a in annotations if a.type == AnnotationType.HIGHLIGHT]
        print(f"   Strokes: {len(strokes)}, Highlights: {len(highlights)}")

        # Extract text origin X
        text_origin_x = processor._get_text_origin_x(rm_file)
        print(f"   Text origin: x={text_origin_x}, y={text_origin_y}")

        # Build parent_id → (anchor_x, baseline_y) mapping via anchor system
        parent_anchor_map = processor._build_parent_baseline_map(rm_file)
        print(f"   Built anchor origin map for {len(parent_anchor_map)} parent IDs")

        # Transform annotations to absolute coordinates using per-parent anchor origins
        annotations_absolute = processor._transform_annotations_to_absolute(
            annotations, parent_anchor_map, text_origin_x, text_origin_y
        )
        print(f"   Transformed {len(annotations)} annotations to absolute coordinates")

        # Run production clustering algorithm (using absolute coordinates)
        clusters = processor._cluster_annotations_by_proximity(annotations_absolute)
        print(f"\n🔍 Clustered into {len(clusters)} groups")
        total_clusters += len(clusters)

        # Analyze each cluster
        for cluster_idx, cluster in enumerate(clusters):
            print(f"\n  Cluster {cluster_idx + 1}:")
            print(f"    • Annotations: {len(cluster)}")

            # Identify cluster types
            cluster_strokes = [a for a in cluster if a.type == AnnotationType.STROKE]
            cluster_highlights = [a for a in cluster if a.type == AnnotationType.HIGHLIGHT]
            print(f"      (Strokes: {len(cluster_strokes)}, Highlights: {len(cluster_highlights)})")

            # Render cluster (already in absolute coordinates)
            try:
                image_data, bbox = processor._render_annotations_to_image(cluster)
                print(f"    • Bbox: x={bbox.x:.1f}, y={bbox.y:.1f}, w={bbox.width:.1f}, h={bbox.height:.1f}")
            except Exception as e:
                print(f"    • ❌ Render failed: {e}")
                continue

            # Map to paragraph (bbox is already in absolute coordinates)
            para_idx = processor.paragraph_mapper.map_cluster_to_paragraph(
                bbox,
                markdown_blocks,
                rm_text_blocks,
            )

            if para_idx is not None:
                md_block = markdown_blocks[para_idx]
                preview = md_block.text[:60].replace("\n", " ")
                print(f"    • ✅ Mapped to paragraph {para_idx}: {md_block.type.name}")
                print(f"       \"{preview}...\"")
            else:
                print(f"    • ❌ No paragraph mapping found")

            # Save PNG image
            if image_data:
                png_filename = f"cluster_{Path(rm_filename).stem}_c{cluster_idx + 1}.png"
                png_path = OUTPUT_DIR / png_filename
                png_path.write_bytes(image_data)
                print(f"    • 💾 Saved: {png_filename}")

    print("\n" + "=" * 80)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 80)
    print(f"\n📊 Total clusters across all files: {total_clusters}")
    print(f"📂 PNG images saved to: {OUTPUT_DIR}")
    print(f"\n🔍 Next steps:")
    print(f"   1. Review PNG images to verify clustering quality")
    print(f"   2. Check paragraph mappings are correct")
    print(f"   3. If clustering looks good, proceed to OCR service test")


if __name__ == "__main__":
    main()
