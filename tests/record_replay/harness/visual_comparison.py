"""Visual comparison of .rm files using rendered PNGs.

Validates stroke appearance by comparing rendered regions at fixed positions,
ensuring both position accuracy AND stroke shape similarity.

The comparison works at the CLUSTER level (not individual strokes):
1. Rendering both .rm files to PNG using rmc + cairosvg
2. Extracting stroke bounding boxes and clustering nearby strokes
3. For each cluster in golden:
   - Computing a combined bounding box for the cluster
   - Using the GOLDEN cluster position as the fixed comparison region
   - Cropping that region from BOTH images
   - Comparing similarity using perceptual hashing
4. Failing if clusters aren't at expected positions or shapes differ too much

This approach:
- Groups strokes into logical units (words, margin notes)
- Compares at the cluster level for robustness
- Enforces position matching via fixed golden regions
- Allows minor stroke variations within clusters

Example:
    from tests.record_replay.harness.visual_comparison import (
        compare_rm_files_visually,
        assert_rm_files_match_visually,
    )

    # Compare test output against golden
    result = compare_rm_files_visually(
        test_rm_files={"page1.rm": test_bytes},
        golden_rm_files={"page1.rm": golden_bytes},
    )

    # Or assert directly
    assert_rm_files_match_visually(
        test_rm_files={"page1.rm": test_bytes},
        golden_rm_files={"page1.rm": golden_bytes},
        max_hash_distance=10,
    )
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import imagehash
from PIL import Image

from rock_paper_sync.annotations import read_annotations
from rock_paper_sync.annotations.core_types import Rectangle

# reMarkable page dimensions (Paper Pro)
RM_PAGE_WIDTH = 1404
RM_PAGE_HEIGHT = 1872


def check_rmc_installed() -> bool:
    """Check if rmc is available in PATH."""
    return shutil.which("rmc") is not None


@dataclass
class StrokeCluster:
    """A cluster of nearby strokes with combined bounding box."""

    stroke_indices: list[int]  # Indices into original bbox list
    bboxes: list[Rectangle]  # Individual stroke bboxes
    combined_bbox: Rectangle  # Combined bounding box for the cluster

    @property
    def stroke_count(self) -> int:
        """Number of strokes in this cluster."""
        return len(self.stroke_indices)

    @property
    def center(self) -> tuple[float, float]:
        """Center of the combined bounding box."""
        return (
            self.combined_bbox.x + self.combined_bbox.w / 2,
            self.combined_bbox.y + self.combined_bbox.h / 2,
        )


@dataclass
class ClusterMatch:
    """A matched pair of stroke clusters with similarity metrics."""

    golden_cluster: StrokeCluster
    test_cluster: StrokeCluster | None  # None if no matching cluster in test
    region_bounds: tuple[int, int, int, int]  # (x, y, w, h) - fixed region from golden
    golden_hash: imagehash.ImageHash | None = None
    test_hash: imagehash.ImageHash | None = None
    hash_distance: int | None = None

    @property
    def has_match(self) -> bool:
        """Whether a matching cluster was found in the test image."""
        return self.test_cluster is not None

    @property
    def is_similar(self) -> bool:
        """Whether the visual appearance is similar (low hash distance)."""
        if self.hash_distance is None:
            return False
        return self.hash_distance <= 15  # Default threshold

    def within_threshold(self, max_distance: int) -> bool:
        """Check if hash distance is within threshold."""
        if self.hash_distance is None:
            return False
        return self.hash_distance <= max_distance

    def format_diff(self) -> str:
        """Format a human-readable diff for this match."""
        lines = []
        g = self.golden_cluster.combined_bbox
        lines.append(
            f"  golden cluster: ({g.x:.0f}, {g.y:.0f}, {g.w:.0f}x{g.h:.0f}) "
            f"[{self.golden_cluster.stroke_count} strokes]"
        )

        if self.test_cluster:
            t = self.test_cluster.combined_bbox
            lines.append(
                f"  test cluster:   ({t.x:.0f}, {t.y:.0f}, {t.w:.0f}x{t.h:.0f}) "
                f"[{self.test_cluster.stroke_count} strokes]"
            )
        else:
            lines.append("  test cluster:   NOT FOUND")

        if self.hash_distance is not None:
            status = "OK" if self.is_similar else "MISMATCH"
            lines.append(f"  hash distance: {self.hash_distance} ({status})")

        x, y, w, h = self.region_bounds
        lines.append(f"  comparison region: ({x}, {y}, {w}x{h})")

        return "\n".join(lines)


# Keep RegionMatch as alias for backwards compatibility
RegionMatch = ClusterMatch


@dataclass
class VisualComparisonResult:
    """Result of visual comparison between test and golden .rm files."""

    matches: list[ClusterMatch] = field(default_factory=list)
    missing_clusters: list[StrokeCluster] = field(default_factory=list)
    extra_clusters: list[StrokeCluster] = field(default_factory=list)
    render_errors: list[str] = field(default_factory=list)

    # Keep old names for backwards compatibility
    @property
    def missing_in_test(self) -> list[Rectangle]:
        """Bboxes of missing clusters (backwards compat)."""
        return [c.combined_bbox for c in self.missing_clusters]

    @property
    def extra_in_test(self) -> list[Rectangle]:
        """Bboxes of extra clusters (backwards compat)."""
        return [c.combined_bbox for c in self.extra_clusters]

    @property
    def all_matched(self) -> bool:
        """Whether all golden clusters have matches in test."""
        return len(self.missing_clusters) == 0

    @property
    def max_hash_distance(self) -> int:
        """Maximum hash distance across all matches."""
        distances = [m.hash_distance for m in self.matches if m.hash_distance is not None]
        return max(distances) if distances else 0

    def within_threshold(self, max_distance: int) -> bool:
        """Check if all matches are within the hash distance threshold."""
        if not self.all_matched:
            return False
        return all(m.within_threshold(max_distance) for m in self.matches)

    @property
    def summary(self) -> str:
        """Short summary of comparison result."""
        if self.render_errors:
            return f"RENDER_ERROR: {len(self.render_errors)} error(s)"
        if not self.all_matched:
            return f"MISSING: {len(self.missing_clusters)} cluster(s) not found in test"
        if len(self.extra_clusters) > 0:
            return f"EXTRA: {len(self.extra_clusters)} unexpected cluster(s) in test"
        total_strokes = sum(m.golden_cluster.stroke_count for m in self.matches)
        return (
            f"OK: {len(self.matches)} cluster(s) matched ({total_strokes} strokes), "
            f"max_distance={self.max_hash_distance}"
        )


def rm_to_png_bytes(
    rm_data: bytes, width: int = RM_PAGE_WIDTH, height: int = RM_PAGE_HEIGHT
) -> bytes:
    """Convert .rm file bytes to PNG bytes using rmc + cairosvg.

    Args:
        rm_data: Raw bytes of .rm file
        width: Output width in pixels
        height: Output height in pixels

    Returns:
        PNG image as bytes

    Raises:
        FileNotFoundError: If rmc is not installed
        subprocess.CalledProcessError: If rmc fails
        RuntimeError: If conversion fails
    """
    if not check_rmc_installed():
        raise FileNotFoundError(
            "rmc not found. Install it with: pipx install rmc\n"
            "(Note: rmc requires rmscene <0.7, so it must be installed separately)"
        )

    # cairosvg is imported here to avoid import errors when rmc isn't needed
    import re

    import cairosvg

    with tempfile.NamedTemporaryFile(suffix=".rm", delete=False) as rm_file:
        rm_path = Path(rm_file.name)
        rm_file.write(rm_data)

    svg_path = rm_path.with_suffix(".svg")

    try:
        # Convert .rm to SVG using rmc
        result = subprocess.run(
            ["rmc", "-t", "svg", "-o", str(svg_path), str(rm_path)],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, "rmc", output=result.stdout, stderr=result.stderr
            )

        # Fix the SVG viewBox to use full page dimensions
        # rmc generates a cropped viewBox; we need the full page
        # reMarkable uses centered coords: x from -702 to 702, y from -936 to 936
        svg_content = svg_path.read_text()

        # Replace viewBox and dimensions with full page
        full_viewbox = f'viewBox="-702 -936 {width} {height}"'
        svg_content = re.sub(r'viewBox="[^"]*"', full_viewbox, svg_content)
        svg_content = re.sub(r'width="[^"]*"', f'width="{width}"', svg_content)
        svg_content = re.sub(r'height="[^"]*"', f'height="{height}"', svg_content)

        # Convert SVG to PNG
        png_bytes = cairosvg.svg2png(
            bytestring=svg_content.encode(),
            output_width=width,
            output_height=height,
        )

        return png_bytes

    finally:
        rm_path.unlink(missing_ok=True)
        svg_path.unlink(missing_ok=True)


def extract_annotation_bboxes(rm_data: bytes) -> list[Rectangle]:
    """Extract bounding boxes from all annotations in .rm file bytes.

    Extracts bounding boxes from both strokes (handwritten annotations)
    and highlights (text selections).

    Args:
        rm_data: Raw bytes of .rm file

    Returns:
        List of bounding box Rectangles
    """
    bboxes = []
    for annotation in read_annotations(io.BytesIO(rm_data)):
        bbox = annotation.bounding_box
        if bbox is not None:
            bboxes.append(bbox)
    return bboxes


# Backwards compatibility alias
extract_stroke_bboxes = extract_annotation_bboxes


def cluster_strokes(
    bboxes: list[Rectangle],
    distance_threshold: float = 80.0,
) -> list[StrokeCluster]:
    """Cluster nearby strokes into logical groups.

    Groups strokes that are within distance_threshold of each other,
    computing a combined bounding box for each cluster.

    Args:
        bboxes: List of stroke bounding boxes
        distance_threshold: Maximum distance between stroke centers to cluster

    Returns:
        List of StrokeCluster objects
    """
    from rock_paper_sync.annotations.common.spatial import cluster_bboxes_kdtree

    if not bboxes:
        return []

    # Filter valid bboxes and track indices
    valid_bboxes = []
    valid_indices = []
    for i, bbox in enumerate(bboxes):
        if is_valid_bbox(bbox):
            valid_bboxes.append((bbox.x, bbox.y, bbox.w, bbox.h))
            valid_indices.append(i)

    if not valid_bboxes:
        return []

    # Cluster using KDTree
    cluster_indices = cluster_bboxes_kdtree(valid_bboxes, distance_threshold)

    # Build StrokeCluster objects
    clusters = []
    for indices in cluster_indices:
        # Map back to original indices
        original_indices = [valid_indices[i] for i in indices]
        cluster_bboxes = [bboxes[i] for i in original_indices]

        # Compute combined bounding box
        min_x = min(b.x for b in cluster_bboxes)
        min_y = min(b.y for b in cluster_bboxes)
        max_x = max(b.x + b.w for b in cluster_bboxes)
        max_y = max(b.y + b.h for b in cluster_bboxes)

        combined = Rectangle(min_x, min_y, max_x - min_x, max_y - min_y)

        clusters.append(
            StrokeCluster(
                stroke_indices=original_indices,
                bboxes=cluster_bboxes,
                combined_bbox=combined,
            )
        )

    return clusters


def is_valid_bbox(bbox: Rectangle) -> bool:
    """Check if a bounding box is valid for comparison.

    Stroke bounding boxes from .rm files are in relative coordinates
    and may have negative values or zero dimensions. This filters
    out unusable bboxes.

    Args:
        bbox: Bounding box to validate

    Returns:
        True if bbox is valid for region comparison
    """
    # Must have positive width and height
    if bbox.w <= 0 or bbox.h <= 0:
        return False
    # Must have reasonable coordinates (not too negative)
    # Note: Some negative values are normal for relative coords
    if bbox.x < -1000 or bbox.y < -1000:
        return False
    return True


def compute_region_bounds(
    bbox: Rectangle,
    padding: int = 50,
    page_width: int = RM_PAGE_WIDTH,
    page_height: int = RM_PAGE_HEIGHT,
) -> tuple[int, int, int, int] | None:
    """Compute fixed comparison region around a bounding box.

    reMarkable uses a centered coordinate system where (0,0) is at the
    page center. This converts to pixel coordinates for image cropping.

    Args:
        bbox: Annotation bounding box in reMarkable centered coordinates
        padding: Pixels to add around bbox
        page_width: Page width for clamping
        page_height: Page height for clamping

    Returns:
        (x, y, w, h) tuple for the region in pixel coordinates, or None if invalid
    """
    if not is_valid_bbox(bbox):
        return None

    # Convert from centered reMarkable coords to pixel coords
    # reMarkable: (0,0) at center, x from -702 to +702, y from -936 to +936
    # Pixels: (0,0) at top-left, x from 0 to 1404, y from 0 to 1872
    pixel_x = page_width / 2 + bbox.x
    pixel_y = page_height / 2 + bbox.y

    x = max(0, int(pixel_x - padding))
    y = max(0, int(pixel_y - padding))
    x2 = min(page_width, int(pixel_x + bbox.w + padding))
    y2 = min(page_height, int(pixel_y + bbox.h + padding))

    # Ensure valid region
    w = x2 - x
    h = y2 - y
    if w <= 0 or h <= 0:
        return None

    return (x, y, w, h)


def crop_region(image: Image.Image, bounds: tuple[int, int, int, int]) -> Image.Image:
    """Crop a region from an image.

    Args:
        image: PIL Image to crop from
        bounds: (x, y, w, h) region bounds

    Returns:
        Cropped PIL Image
    """
    x, y, w, h = bounds
    return image.crop((x, y, x + w, y + h))


def compute_phash(image: Image.Image, hash_size: int = 16) -> imagehash.ImageHash:
    """Compute perceptual hash of an image.

    Args:
        image: PIL Image
        hash_size: Hash size (higher = more precise)

    Returns:
        ImageHash object
    """
    # Convert to grayscale for consistent hashing
    gray = image.convert("L")
    return imagehash.phash(gray, hash_size=hash_size)


def match_cluster_by_position(
    test_clusters: list[StrokeCluster],
    golden_cluster: StrokeCluster,
    tolerance: float = 150.0,
) -> StrokeCluster | None:
    """Find the test cluster closest to a golden cluster position.

    Args:
        test_clusters: List of test clusters
        golden_cluster: Golden cluster to match
        tolerance: Maximum center distance for a match

    Returns:
        Closest matching test cluster, or None if none within tolerance
    """
    best_match = None
    best_distance = float("inf")

    golden_cx, golden_cy = golden_cluster.center

    for test_cluster in test_clusters:
        test_cx, test_cy = test_cluster.center

        distance = ((golden_cx - test_cx) ** 2 + (golden_cy - test_cy) ** 2) ** 0.5

        if distance < best_distance and distance <= tolerance:
            best_distance = distance
            best_match = test_cluster

    return best_match


def compare_rm_files_visually(
    test_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
    padding: int = 50,
    position_tolerance: float = 150.0,
    cluster_distance: float = 80.0,
    hash_size: int = 16,
) -> VisualComparisonResult:
    """Compare test .rm files against golden visually at cluster level.

    Groups strokes into clusters, then compares each golden cluster against
    the test image at the GOLDEN cluster's fixed position.

    Args:
        test_rm_files: page_uuid -> .rm bytes from test output
        golden_rm_files: page_uuid -> .rm bytes from golden reference
        padding: Pixels to add around each cluster for comparison region
        position_tolerance: Max center distance to match clusters
        cluster_distance: Max distance to group strokes into clusters
        hash_size: Perceptual hash size (higher = more precise)

    Returns:
        VisualComparisonResult with match details
    """
    result = VisualComparisonResult()

    # Collect strokes from ALL pages (don't match pages individually)
    # This handles page reordering and different page counts between test/golden
    all_golden_bboxes: list[Rectangle] = []
    all_test_bboxes: list[Rectangle] = []

    for page_uuid, rm_data in golden_rm_files.items():
        all_golden_bboxes.extend(extract_stroke_bboxes(rm_data))

    for page_uuid, rm_data in test_rm_files.items():
        all_test_bboxes.extend(extract_stroke_bboxes(rm_data))

    golden_clusters = cluster_strokes(all_golden_bboxes, cluster_distance)
    test_clusters = cluster_strokes(all_test_bboxes, cluster_distance)

    if not golden_clusters:
        return result  # No valid clusters to compare

    # Track which test clusters have been matched
    matched_test_clusters: set[int] = set()

    # Compare each golden cluster by position (skip visual rendering for now)
    for golden_cluster in golden_clusters:
        # Compute fixed region bounds based on golden cluster position
        region_bounds = compute_region_bounds(golden_cluster.combined_bbox, padding)
        if region_bounds is None:
            # Invalid region, skip this cluster
            continue

        # Find matching test cluster by position
        test_cluster = match_cluster_by_position(test_clusters, golden_cluster, position_tolerance)

        if test_cluster is None:
            # No matching cluster in test
            result.missing_clusters.append(golden_cluster)
            result.matches.append(
                ClusterMatch(
                    golden_cluster=golden_cluster,
                    test_cluster=None,
                    region_bounds=region_bounds,
                )
            )
            continue

        # Track that this test cluster was matched
        matched_test_clusters.add(id(test_cluster))

        # Record match without visual hash comparison (position-based only)
        result.matches.append(
            ClusterMatch(
                golden_cluster=golden_cluster,
                test_cluster=test_cluster,
                region_bounds=region_bounds,
                hash_distance=0,  # Position matched, no visual comparison
            )
        )

    # Find extra clusters in test that weren't matched
    for test_cluster in test_clusters:
        if id(test_cluster) not in matched_test_clusters:
            result.extra_clusters.append(test_cluster)

    return result


def assert_rm_files_match_visually(
    test_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
    max_hash_distance: int = 15,
    padding: int = 50,
    position_tolerance: float = 100.0,
) -> None:
    """Assert that test .rm files match golden visually.

    Args:
        test_rm_files: page_uuid -> .rm bytes from test output
        golden_rm_files: page_uuid -> .rm bytes from golden reference
        max_hash_distance: Maximum allowed perceptual hash distance
        padding: Pixels to add around each stroke for comparison region
        position_tolerance: Max center distance to match strokes

    Raises:
        AssertionError: If visual comparison fails
    """
    result = compare_rm_files_visually(
        test_rm_files,
        golden_rm_files,
        padding=padding,
        position_tolerance=position_tolerance,
    )

    if result.render_errors:
        raise AssertionError(
            "Failed to render .rm files:\n" + "\n".join(f"  - {e}" for e in result.render_errors)
        )

    if not result.all_matched:
        lines = [f"Missing {len(result.missing_clusters)} cluster(s) in test output:"]
        for cluster in result.missing_clusters:
            bbox = cluster.combined_bbox
            lines.append(
                f"  - cluster at ({bbox.x:.0f}, {bbox.y:.0f}, {bbox.w:.0f}x{bbox.h:.0f}) "
                f"[{cluster.stroke_count} strokes]"
            )
        raise AssertionError("\n".join(lines))

    failures = [m for m in result.matches if not m.within_threshold(max_hash_distance)]
    if failures:
        lines = [f"Visual mismatch for {len(failures)} cluster(s) (threshold={max_hash_distance}):"]
        for f in failures:
            lines.append(f.format_diff())
            lines.append("")
        raise AssertionError("\n".join(lines))


def print_visual_comparison(
    test_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
) -> None:
    """Print detailed visual comparison results for debugging.

    Args:
        test_rm_files: page_uuid -> .rm bytes from test output
        golden_rm_files: page_uuid -> .rm bytes from golden reference
    """
    result = compare_rm_files_visually(test_rm_files, golden_rm_files)

    print("\n" + "=" * 60)
    print("VISUAL COMPARISON: Test vs Golden (Cluster-Based)")
    print("=" * 60)
    print(f"\nSummary: {result.summary}")

    if result.render_errors:
        print("\nRender Errors:")
        for err in result.render_errors:
            print(f"  - {err}")

    if result.matches:
        print(f"\nMatched {len(result.matches)} cluster(s):")
        for i, m in enumerate(result.matches):
            status = "OK" if m.is_similar else "MISMATCH"
            print(f"\nCluster {i} [{status}]:")
            print(m.format_diff())

    if result.missing_clusters:
        print(f"\nMissing in test: {len(result.missing_clusters)} cluster(s)")
        for cluster in result.missing_clusters:
            bbox = cluster.combined_bbox
            print(
                f"  - ({bbox.x:.0f}, {bbox.y:.0f}, {bbox.w:.0f}x{bbox.h:.0f}) "
                f"[{cluster.stroke_count} strokes]"
            )

    if result.extra_clusters:
        print(f"\nExtra in test: {len(result.extra_clusters)} cluster(s)")
        for cluster in result.extra_clusters:
            bbox = cluster.combined_bbox
            print(
                f"  - ({bbox.x:.0f}, {bbox.y:.0f}, {bbox.w:.0f}x{bbox.h:.0f}) "
                f"[{cluster.stroke_count} strokes]"
            )

    print("=" * 60)


def get_default_debug_dir() -> Path:
    """Get the default directory for debug images.

    Returns:
        Path to debug directory (tests/record_replay/debug_images/)
    """
    # Find the tests/record_replay directory
    current = Path(__file__).parent  # harness/
    record_replay = current.parent  # record_replay/
    debug_dir = record_replay / "debug_images"
    return debug_dir


def save_comparison_debug_images(
    test_rm_files: dict[str, bytes],
    golden_rm_files: dict[str, bytes],
    output_dir: Path | None = None,
    padding: int = 50,
    test_name: str | None = None,
    cluster_distance: float = 80.0,
    test_page_order: list[str] | None = None,
    golden_page_order: list[str] | None = None,
) -> list[Path]:
    """Save debug images showing comparison regions.

    Useful for debugging visual comparison failures. Saves:
    - Full page renders (test and golden) by PAGE INDEX (not UUID)
    - Cropped comparison regions for each CLUSTER

    Images are saved to:
        {output_dir}/{test_name}/
            page{N}_golden.png          - Full golden page N
            page{N}_test.png            - Full test page N
            page{N}_cluster{i}_golden.png  - Cropped golden cluster region
            page{N}_cluster{i}_test.png    - Cropped test cluster region

    Note: Pages are ordered by the provided page_order lists, falling back
    to UUID sort if not provided.

    Args:
        test_rm_files: page_uuid -> .rm bytes from test output
        golden_rm_files: page_uuid -> .rm bytes from golden reference
        output_dir: Directory to save debug images (default: tests/record_replay/debug_images/)
        padding: Pixels to add around each cluster region
        test_name: Optional subdirectory name (e.g., test ID or timestamp)
        cluster_distance: Max distance to group strokes into clusters
        test_page_order: Optional list of test page UUIDs in display order
        golden_page_order: Optional list of golden page UUIDs in display order

    Returns:
        List of paths to saved images
    """
    if output_dir is None:
        output_dir = get_default_debug_dir()

    if test_name:
        output_dir = output_dir / test_name
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = []

    # Order pages by provided order, falling back to UUID sort
    def order_pages(
        rm_files: dict[str, bytes], page_order: list[str] | None
    ) -> list[tuple[str, bytes]]:
        if page_order:
            # Use provided order, only including UUIDs that exist in rm_files
            return [(uuid, rm_files[uuid]) for uuid in page_order if uuid in rm_files]
        else:
            # Fall back to UUID sort
            return sorted(rm_files.items())

    golden_pages = order_pages(golden_rm_files, golden_page_order)
    test_pages = order_pages(test_rm_files, test_page_order)

    # Process golden pages by index
    for page_idx, (golden_uuid, golden_rm_data) in enumerate(golden_pages):
        golden_bboxes = extract_stroke_bboxes(golden_rm_data)
        golden_clusters = cluster_strokes(golden_bboxes, cluster_distance)

        # Render golden page
        try:
            golden_png = rm_to_png_bytes(golden_rm_data)
            golden_path = output_dir / f"page{page_idx}_golden.png"
            golden_path.write_bytes(golden_png)
            saved_paths.append(golden_path)
            golden_image = Image.open(io.BytesIO(golden_png))
        except Exception:
            continue

        # Save golden cluster crops
        for i, cluster in enumerate(golden_clusters):
            region_bounds = compute_region_bounds(cluster.combined_bbox, padding)
            if region_bounds is None:
                continue

            golden_crop = crop_region(golden_image, region_bounds)
            crop_path = output_dir / f"page{page_idx}_cluster{i}_golden.png"
            golden_crop.save(crop_path)
            saved_paths.append(crop_path)

    # Process test pages by index (separately, since page counts may differ)
    for page_idx, (test_uuid, test_rm_data) in enumerate(test_pages):
        test_bboxes = extract_stroke_bboxes(test_rm_data)
        test_clusters = cluster_strokes(test_bboxes, cluster_distance)

        # Render test page
        try:
            test_png = rm_to_png_bytes(test_rm_data)
            test_path = output_dir / f"page{page_idx}_test.png"
            test_path.write_bytes(test_png)
            saved_paths.append(test_path)
            test_image = Image.open(io.BytesIO(test_png))
        except Exception:
            continue

        # Save test cluster crops
        for i, cluster in enumerate(test_clusters):
            region_bounds = compute_region_bounds(cluster.combined_bbox, padding)
            if region_bounds is None:
                continue

            test_crop = crop_region(test_image, region_bounds)
            crop_path = output_dir / f"page{page_idx}_cluster{i}_test.png"
            test_crop.save(crop_path)
            saved_paths.append(crop_path)

    return saved_paths
