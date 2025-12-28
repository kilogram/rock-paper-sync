"""Tests for visual comparison of .rm files.

This module tests the visual_comparison module which uses PNG rendering
and perceptual hashing to compare stroke appearance between test and golden
.rm files.
"""

import pytest

from tests.record_replay.harness.visual_comparison import (
    VisualComparisonResult,
    compare_rm_files_visually,
    extract_stroke_bboxes,
    print_visual_comparison,
    rm_to_png_bytes_renderer,
)


@pytest.fixture
def cross_page_testdata(testdata_store):
    """Load cross_page_reanchor testdata if available."""
    test_id = "cross_page_reanchor"
    if not testdata_store.has_trips(test_id):
        pytest.skip(f"No testdata for {test_id}")

    trips = testdata_store.load_trips(test_id)
    golden = testdata_store.get_golden(test_id)
    trip_1 = testdata_store.get_trip(test_id, 1)

    return {
        "test_id": test_id,
        "trips": trips,
        "golden": golden,
        "trip_1": trip_1,
    }


def test_rm_to_png_renders_file(cross_page_testdata):
    """Test that .rm files can be rendered to PNG using our custom renderer."""
    golden = cross_page_testdata["golden"]
    if not golden or not golden.annotations:
        pytest.skip("No golden annotations")

    # Get first .rm file
    rm_files = golden.annotations.rm_files
    if not rm_files:
        pytest.skip("No .rm files in golden")

    page_uuid, rm_data = next(iter(rm_files.items()))

    # Render to PNG using our renderer
    png_bytes = rm_to_png_bytes_renderer(rm_data)

    assert png_bytes is not None
    assert len(png_bytes) > 0
    # PNG magic bytes
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_extract_stroke_bboxes(cross_page_testdata):
    """Test extracting stroke bounding boxes from .rm files."""
    golden = cross_page_testdata["golden"]
    if not golden or not golden.annotations:
        pytest.skip("No golden annotations")

    rm_files = golden.annotations.rm_files
    if not rm_files:
        pytest.skip("No .rm files in golden")

    # Find a file with strokes
    total_strokes = 0
    for page_uuid, rm_data in rm_files.items():
        bboxes = extract_stroke_bboxes(rm_data)
        total_strokes += len(bboxes)

        # Verify bbox structure
        for bbox in bboxes:
            assert hasattr(bbox, "x")
            assert hasattr(bbox, "y")
            assert hasattr(bbox, "w")
            assert hasattr(bbox, "h")

    print(f"\nTotal strokes across all pages: {total_strokes}")


def test_compare_identical_files(cross_page_testdata):
    """Test that comparing identical files returns perfect match."""
    golden = cross_page_testdata["golden"]
    if not golden or not golden.annotations:
        pytest.skip("No golden annotations")

    rm_files = golden.annotations.rm_files
    if not rm_files:
        pytest.skip("No .rm files in golden")

    # Compare golden against itself
    result = compare_rm_files_visually(rm_files, rm_files)

    print(f"\nComparison result: {result.summary}")

    # Should have no render errors
    assert len(result.render_errors) == 0, f"Render errors: {result.render_errors}"

    # All strokes should match
    assert result.all_matched, f"Missing strokes: {len(result.missing_in_test)}"

    # Hash distance should be 0 for identical files
    if result.matches:
        assert result.max_hash_distance == 0, f"Max hash distance: {result.max_hash_distance}"


def test_compare_trip_vs_golden(cross_page_testdata):
    """Test comparing trip annotations against golden."""
    golden = cross_page_testdata["golden"]
    trip_1 = cross_page_testdata["trip_1"]

    if not golden or not golden.annotations:
        pytest.skip("No golden annotations")
    if not trip_1 or not trip_1.annotations:
        pytest.skip("No trip 1 annotations")

    golden_rm = golden.annotations.rm_files
    trip_rm = trip_1.annotations.rm_files

    if not golden_rm or not trip_rm:
        pytest.skip("Missing .rm files")

    # Compare trip against golden
    result = compare_rm_files_visually(trip_rm, golden_rm)

    print("\n" + "=" * 60)
    print("TRIP 1 VS GOLDEN COMPARISON")
    print("=" * 60)
    print(f"Summary: {result.summary}")
    print(f"Matches: {len(result.matches)}")
    print(f"Missing in test: {len(result.missing_in_test)}")
    print(f"Extra in test: {len(result.extra_in_test)}")
    if result.matches:
        print(f"Max hash distance: {result.max_hash_distance}")

    # Print detailed comparison if there are matches
    if result.matches:
        for i, match in enumerate(result.matches):
            status = "OK" if match.is_similar else "DIFF"
            print(f"\n  Match {i} [{status}]:")
            g = match.golden_cluster.combined_bbox
            print(
                f"    Golden: ({g.x:.0f}, {g.y:.0f}) [{match.golden_cluster.stroke_count} strokes]"
            )
            if match.test_cluster:
                t = match.test_cluster.combined_bbox
                print(
                    f"    Test:   ({t.x:.0f}, {t.y:.0f}) [{match.test_cluster.stroke_count} strokes]"
                )
            if match.hash_distance is not None:
                print(f"    Hash distance: {match.hash_distance}")


def test_visual_validator_fixture(visual_validator, cross_page_testdata):
    """Test the visual_validator fixture."""
    golden = cross_page_testdata["golden"]
    if not golden or not golden.annotations:
        pytest.skip("No golden annotations")

    rm_files = golden.annotations.rm_files
    if not rm_files:
        pytest.skip("No .rm files in golden")

    # Test compare method
    result = visual_validator.compare(rm_files, rm_files)
    assert result is not None
    assert isinstance(result, VisualComparisonResult)

    # Test print_comparison (should not raise)
    visual_validator.print_comparison(rm_files, rm_files)

    # Test assert_visual_match (should not raise for identical files)
    visual_validator.assert_visual_match(rm_files, rm_files)


def test_print_visual_comparison(cross_page_testdata, capsys):
    """Test print_visual_comparison output."""
    golden = cross_page_testdata["golden"]
    if not golden or not golden.annotations:
        pytest.skip("No golden annotations")

    rm_files = golden.annotations.rm_files
    if not rm_files:
        pytest.skip("No .rm files in golden")

    # Print comparison
    print_visual_comparison(rm_files, rm_files)

    # Check output contains expected sections
    captured = capsys.readouterr()
    assert "VISUAL COMPARISON" in captured.out
    assert "Summary:" in captured.out
