#!/usr/bin/env python3
"""Test different coordinate transformation rules for reMarkable strokes.

This tool systematically tests various transformation approaches to validate
the correct method for converting native stroke coordinates to absolute page
coordinates. Used to verify the 60px offset solution documented in STROKE_ANCHORING.md.

Usage:
    # Test all transformation rules
    python transform_tester.py --manifest path/to/manifest.json

    # Test specific rule
    python transform_tester.py --manifest manifest.json --rule simple_offset

    # Custom output directory
    python transform_tester.py --manifest manifest.json --output-dir ./results

    # Test with specific .rm files
    python transform_tester.py --rm-files file1.rm file2.rm --output-dir ./test

Transformation Rules:
    native          - No transformation (baseline)
    x_only          - Apply X offset only, no Y transformation
    simple_offset   - Fixed 60px offset for negative Y (CORRECT SOLUTION)
    offset_30       - Test 30px offset variant
    offset_90       - Test 90px offset variant
    adaptive        - Adaptive scaling (0.534 factor) - over-complex
    no_scale        - Fixed 56.3px without scaling

The tool renders each transformation to PNG images for visual comparison.
The 'simple_offset' rule should produce correct results matching actual
handwriting positions.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rock_paper_sync.annotations import read_annotations
from rock_paper_sync.annotation_mapper import extract_text_blocks_from_rm


TRANSFORMATION_RULES = {
    'native': 'No transformation',
    'x_only': 'X offset only',
    'simple_offset': 'Fixed 60px for negative Y (CORRECT)',
    'offset_30': '30px offset',
    'offset_90': '90px offset',
    'adaptive': 'Adaptive 0.534 scaling',
    'no_scale': 'Fixed 56.3px offset',
}


def transform_coordinates(annotations, rule, text_origin_x, text_origin_y, parent_anchor_map):
    """Apply transformation rule to annotations.

    Args:
        annotations: List of annotations with strokes
        rule: Transformation rule name
        text_origin_x: X origin from RootTextBlock
        text_origin_y: Y origin from RootTextBlock
        parent_anchor_map: Dict mapping parent_id to (anchor_x, anchor_y)

    Returns:
        List of transformed annotations
    """
    from rock_paper_sync.annotations import Annotation, Point, Stroke

    transformed = []

    for ann in annotations:
        if not ann.stroke:
            transformed.append(ann)
            continue

        # Get anchor for this parent
        anchor_x = text_origin_x
        if ann.parent_id in parent_anchor_map:
            anchor_x, _ = parent_anchor_map[ann.parent_id]

        # Calculate stroke center Y for rules that need it
        bbox = ann.stroke.bounding_box
        stroke_center_y = bbox.y + bbox.h / 2

        # Apply transformation based on rule
        new_points = []
        for point in ann.stroke.points:
            if rule == 'native':
                # No transformation
                new_x, new_y = point.x, point.y

            elif rule == 'x_only':
                # X transformation only
                new_x = point.x + anchor_x
                new_y = point.y

            elif rule == 'simple_offset':
                # Fixed 60px offset for negative Y (CORRECT SOLUTION)
                NEGATIVE_Y_OFFSET = 60
                new_x = point.x + anchor_x
                if stroke_center_y >= 0:
                    new_y = text_origin_y + point.y
                else:
                    new_y = text_origin_y + NEGATIVE_Y_OFFSET + point.y

            elif rule == 'offset_30':
                # Test 30px offset
                new_x = point.x + anchor_x
                if stroke_center_y >= 0:
                    new_y = text_origin_y + point.y
                else:
                    new_y = text_origin_y + 30 + point.y

            elif rule == 'offset_90':
                # Test 90px offset
                new_x = point.x + anchor_x
                if stroke_center_y >= 0:
                    new_y = text_origin_y + point.y
                else:
                    new_y = text_origin_y + 90 + point.y

            elif rule == 'adaptive':
                # Adaptive scaling (over-complex, was debugging artifact)
                new_x = point.x + anchor_x
                if stroke_center_y >= 0:
                    new_y = text_origin_y + point.y
                else:
                    offset = 56.3 - 0.534 * stroke_center_y
                    new_y = text_origin_y + offset + point.y

            elif rule == 'no_scale':
                # Fixed offset without scaling
                new_x = point.x + anchor_x
                if stroke_center_y >= 0:
                    new_y = text_origin_y + point.y
                else:
                    new_y = text_origin_y + 56.3 + point.y

            else:
                raise ValueError(f"Unknown rule: {rule}")

            new_points.append(Point(x=new_x, y=new_y))

        new_stroke = Stroke(
            points=new_points,
            color=ann.stroke.color,
            thickness=ann.stroke.thickness,
        )

        transformed.append(Annotation(
            annotation_type=ann.annotation_type,
            stroke=new_stroke,
            parent_id=ann.parent_id,
        ))

    return transformed


def render_annotations(annotations, output_path, width=1404, height=1872):
    """Render annotations to PNG image.

    Args:
        annotations: List of transformed annotations
        output_path: Path to save PNG
        width: Image width (default: reMarkable page width)
        height: Image height (default: reMarkable page height)
    """
    # Create white background
    img = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.draw(img)

    for ann in annotations:
        if not ann.stroke or not ann.stroke.points:
            continue

        # Draw stroke as connected lines
        points = [(p.x, p.y) for p in ann.stroke.points]
        if len(points) > 1:
            draw.line(points, fill='black', width=2)

    img.save(output_path)
    print(f"Saved: {output_path}")


def build_parent_anchor_map(rm_file):
    """Extract anchor_origin_x for each parent from TreeNodeBlocks.

    Args:
        rm_file: Path to .rm file

    Returns:
        Dict mapping parent CrdtId to (anchor_x, 0)
    """
    import rmscene
    from rmscene.scene_stream import TreeNodeBlock

    parent_to_anchor = {}

    with rm_file.open('rb') as f:
        blocks = list(rmscene.read_blocks(f))

    for block in blocks:
        if not isinstance(block, TreeNodeBlock):
            continue
        if not hasattr(block, 'group'):
            continue

        node_id = block.group.node_id
        if hasattr(block.group, 'anchor_origin_x') and block.group.anchor_origin_x:
            anchor_x = block.group.anchor_origin_x.value if hasattr(block.group.anchor_origin_x, 'value') else block.group.anchor_origin_x
            parent_to_anchor[node_id] = (anchor_x, 0)

    return parent_to_anchor


def test_transformations_from_manifest(manifest_path, rules=None, output_dir=None):
    """Test transformations on files listed in manifest.

    Args:
        manifest_path: Path to manifest.json
        rules: List of rule names to test (default: all)
        output_dir: Output directory for PNGs (default: transformation_tests/)
    """
    manifest = json.loads(manifest_path.read_text())
    base_dir = manifest_path.parent

    if output_dir is None:
        output_dir = base_dir / 'transformation_tests'

    if rules is None:
        rules = list(TRANSFORMATION_RULES.keys())

    # Process each file
    for file_info in manifest['files']:
        rm_file = base_dir / file_info['filename']
        if not rm_file.exists():
            print(f"Warning: {rm_file} not found, skipping")
            continue

        print(f"\nProcessing: {rm_file.name}")

        # Read annotations and extract text origin
        annotations = read_annotations(rm_file)
        _, text_origin_y = extract_text_blocks_from_rm(rm_file)
        text_origin_x = -375.0  # Standard reMarkable text origin
        parent_anchor_map = build_parent_anchor_map(rm_file)

        print(f"  Annotations: {len(annotations)}")
        print(f"  Text origin: ({text_origin_x}, {text_origin_y})")
        print(f"  Parent anchors: {len(parent_anchor_map)}")

        # Test each rule
        for rule in rules:
            rule_dir = output_dir / rule
            rule_dir.mkdir(parents=True, exist_ok=True)

            print(f"  Testing rule: {rule}")
            transformed = transform_coordinates(
                annotations, rule, text_origin_x, text_origin_y, parent_anchor_map
            )

            output_file = rule_dir / f"{rm_file.stem}.png"
            render_annotations(transformed, output_file)


def test_transformations_from_files(rm_files, rules=None, output_dir=None):
    """Test transformations on specific .rm files.

    Args:
        rm_files: List of .rm file paths
        rules: List of rule names to test (default: all)
        output_dir: Output directory for PNGs (default: ./transformation_tests/)
    """
    if output_dir is None:
        output_dir = Path('./transformation_tests')

    if rules is None:
        rules = list(TRANSFORMATION_RULES.keys())

    for rm_file in rm_files:
        if not rm_file.exists():
            print(f"Warning: {rm_file} not found, skipping")
            continue

        print(f"\nProcessing: {rm_file.name}")

        annotations = read_annotations(rm_file)
        _, text_origin_y = extract_text_blocks_from_rm(rm_file)
        text_origin_x = -375.0
        parent_anchor_map = build_parent_anchor_map(rm_file)

        for rule in rules:
            rule_dir = output_dir / rule
            rule_dir.mkdir(parents=True, exist_ok=True)

            transformed = transform_coordinates(
                annotations, rule, text_origin_x, text_origin_y, parent_anchor_map
            )

            output_file = rule_dir / f"{rm_file.stem}.png"
            render_annotations(transformed, output_file)


def main():
    parser = argparse.ArgumentParser(
        description='Test coordinate transformation rules',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        '--manifest',
        type=Path,
        help='Path to manifest.json file'
    )
    parser.add_argument(
        '--rm-files',
        type=Path,
        nargs='+',
        help='Specific .rm files to test'
    )
    parser.add_argument(
        '--rule',
        choices=list(TRANSFORMATION_RULES.keys()),
        help='Test specific rule only (default: all rules)'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        help='Output directory for PNG images'
    )
    parser.add_argument(
        '--list-rules',
        action='store_true',
        help='List available transformation rules and exit'
    )

    args = parser.parse_args()

    if args.list_rules:
        print("Available transformation rules:")
        for rule, desc in TRANSFORMATION_RULES.items():
            print(f"  {rule:15} - {desc}")
        return 0

    if not args.manifest and not args.rm_files:
        parser.error("Either --manifest or --rm-files required")

    rules = [args.rule] if args.rule else None

    if args.manifest:
        test_transformations_from_manifest(args.manifest, rules, args.output_dir)
    else:
        test_transformations_from_files(args.rm_files, rules, args.output_dir)

    return 0


if __name__ == '__main__':
    sys.exit(main())
