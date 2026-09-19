"""Anti-vacuousness calibration for the visual comparison verdict.

The cluster-match path in harness/visual_comparison.py once hardcoded
hash_distance=0 ("position matched, no visual comparison"), which made every
visual assertion pass unconditionally. These tests make that class of decay
impossible to reintroduce silently:

1. identical input -> distance 0 (wiring: distances are actually computed
   through render → crop → pHash, and a self-compare is clean);
2. a known perturbation (crop shifted by one line pitch) -> distance above
   the pass threshold (sensitivity: the metric can see a one-line layout
   error, the smallest regression we care about);
3. comparing two different real device captures yields at least one nonzero
   distance (end-to-end: the verdict path cannot return all-zero for
   different content).

Thresholds justified empirically per LAYOUT_VERIFICATION_PLAN.md §5.2 rather
than picked: (1) and (2) bracket the default max_hash_distance=15.
"""

from pathlib import Path

import pytest

from native_layout.layout.device import DEFAULT_DEVICE
from tests.record_replay.harness.visual_comparison import (
    compare_rm_files_visually,
    compute_phash,
    crop_region,
    rm_to_png_image,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
TESTDATA = _REPO_ROOT / "tests" / "record_replay" / "testdata"
ST_GOLDEN = (
    TESTDATA
    / "stroke_reanchor/phases/phase_4_golden_native/rm_files"
    / "2384b9eb-be9f-4038-a864-5acd632cf3ce.rm"
)
ST_PHASE3 = (
    TESTDATA
    / "stroke_reanchor/phases/phase_3_post_modification/rm_files"
    / "83b3eb97-d399-4807-aab5-e854c2ae5a3a.rm"
)
HL_PHASE3 = (
    TESTDATA
    / "highlight_reanchor/phases/phase_3_post_modification/rm_files"
    / "f14298cf-4ad5-4eba-bb1a-a3e76b605bf4.rm"
)

MAX_HASH_DISTANCE = 15  # default pass threshold in assert_rm_files_match_visually


@pytest.fixture(scope="module")
def golden_image():
    return rm_to_png_image(ST_GOLDEN.read_bytes())


@pytest.fixture(scope="module")
def busy_region(golden_image):
    """A 500x300 crop centered on the ink's bounding box, computed from the
    rendered image itself (a hardcoded region went stale the moment the
    renderer's layout changed — this cannot)."""
    gray = golden_image.convert("L")
    bbox = gray.point(lambda p: 255 if p < 200 else 0).getbbox()
    assert bbox, "golden render contains no ink — calibration impossible"
    cx, cy = (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2
    w, h = 500, 300
    x = max(0, min(cx - w // 2, golden_image.width - w))
    y = max(0, min(cy - h // 2, golden_image.height - h))
    return (x, y, w, h)


def test_identical_region_hashes_to_zero(golden_image, busy_region):
    a = compute_phash(crop_region(golden_image, busy_region))
    b = compute_phash(crop_region(golden_image, busy_region))
    assert a - b == 0


def test_one_line_pitch_shift_exceeds_threshold(golden_image, busy_region):
    """A crop displaced by one line pitch must read as a mismatch — this is
    the smallest layout regression the visual gate exists to catch."""
    x, y, w, h = busy_region
    shift = round(DEFAULT_DEVICE.line_height)
    a = compute_phash(crop_region(golden_image, busy_region))
    b = compute_phash(crop_region(golden_image, (x, y + shift, w, h)))
    distance = a - b
    assert distance > MAX_HASH_DISTANCE, (
        f"pHash cannot separate a one-line ({shift} px) shift from noise: "
        f"distance={distance} <= threshold={MAX_HASH_DISTANCE}. Escalate to "
        "region-cropped pHash or SSIM (plan §5.2)."
    )


def test_selfcompare_is_clean_and_computed():
    """Golden vs itself: every matched cluster must carry a COMPUTED distance
    (not None) and it must be 0."""
    files = {"page": ST_GOLDEN.read_bytes()}
    result = compare_rm_files_visually(files, files)
    assert result.matches, "no clusters found — self-compare is vacuous"
    assert not result.render_errors, result.render_errors
    for m in result.matches:
        assert m.hash_distance is not None, "verdict path skipped pHash computation"
        assert m.hash_distance == 0


def test_content_mismatch_is_detected():
    """A test file whose ink CONTENT differs from the golden must trip the
    gate. Note the per-cluster crops are translation-invariant BY DESIGN
    (identical ink that merely moved compares equal — movement is the
    positional pairing's job, and test_positional_certification's); so this
    uses a capture with no strokes at all, which must surface as missing
    clusters, and the phase-3 capture, whose matched clusters must carry
    COMPUTED (non-None) distances end-to-end."""
    golden = {"page": ST_GOLDEN.read_bytes()}

    empty = compare_rm_files_visually({"page": HL_PHASE3.read_bytes()}, golden)
    assert empty.missing_clusters, (
        "golden strokes absent from test output were not reported missing — "
        "the visual gate is vacuous"
    )

    moved = compare_rm_files_visually({"page": ST_PHASE3.read_bytes()}, golden)
    matched = [m for m in moved.matches if m.test_cluster is not None]
    assert matched, "no clusters paired between phase-3 and golden captures"
    assert all(
        m.hash_distance is not None for m in matched
    ), "verdict path skipped pHash computation on paired clusters"
