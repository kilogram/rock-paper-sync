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

from rock_paper_sync.layout.device import DEFAULT_DEVICE
from tests.record_replay.harness.visual_comparison import (
    compare_rm_files_visually,
    compute_phash,
    crop_region,
    rm_to_png_image,
)

TESTDATA = Path(__file__).parent / "testdata"
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

MAX_HASH_DISTANCE = 15  # default pass threshold in assert_rm_files_match_visually

# A busy region of the golden capture (strokes around y≈900-1100 in pixel
# coords; the capture has 59 stroke lines) used for the sensitivity check.
REGION = (300, 850, 500, 300)


@pytest.fixture(scope="module")
def golden_image():
    return rm_to_png_image(ST_GOLDEN.read_bytes())


def test_identical_region_hashes_to_zero(golden_image):
    a = compute_phash(crop_region(golden_image, REGION))
    b = compute_phash(crop_region(golden_image, REGION))
    assert a - b == 0


def test_one_line_pitch_shift_exceeds_threshold(golden_image):
    """A crop displaced by one line pitch must read as a mismatch — this is
    the smallest layout regression the visual gate exists to catch."""
    x, y, w, h = REGION
    shift = round(DEFAULT_DEVICE.line_height)
    a = compute_phash(crop_region(golden_image, REGION))
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


def test_different_captures_produce_nonzero_distance():
    """Phase-3 capture as test output vs the golden capture (the strokes sit
    at visibly different positions): the verdict path must not return
    all-zero distances."""
    result = compare_rm_files_visually(
        {"page": ST_PHASE3.read_bytes()},  # test
        {"page": ST_GOLDEN.read_bytes()},  # golden
    )
    computed = [m.hash_distance for m in result.matches if m.hash_distance is not None]
    mismatch_signal = (
        any(d > 0 for d in computed) or result.missing_clusters or result.extra_clusters
    )
    assert mismatch_signal, (
        "comparing different captures produced neither a nonzero pHash "
        "distance nor missing/extra clusters — the visual gate is vacuous"
    )
