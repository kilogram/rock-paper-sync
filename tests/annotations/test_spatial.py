"""Tests for spatial utilities (clustering, paragraph matching)."""

import pytest

from rock_paper_sync.annotations.common.spatial import (
    ClusteringStrategy,
    KDTreeProximityStrategy,
    StrokeData,
    VisualModelStrategy,
    cluster_bboxes_kdtree,
    cluster_by_proximity,
    find_nearest_paragraph_by_y,
    get_clustering_strategy,
)


class TestClusterByProximity:
    """Tests for cluster_by_proximity function."""

    def test_empty_centers(self):
        """Empty input returns empty result."""
        result = cluster_by_proximity([])
        assert result == []

    def test_single_point(self):
        """Single point returns single cluster with that index."""
        result = cluster_by_proximity([(100.0, 200.0)])
        assert result == [[0]]

    def test_two_close_points(self):
        """Two close points cluster together."""
        centers = [(0.0, 0.0), (10.0, 10.0)]
        result = cluster_by_proximity(centers, distance_threshold=20.0)
        # Should be one cluster with both indices
        assert len(result) == 1
        assert set(result[0]) == {0, 1}

    def test_two_distant_points(self):
        """Two distant points form separate clusters."""
        centers = [(0.0, 0.0), (100.0, 100.0)]
        result = cluster_by_proximity(centers, distance_threshold=20.0)
        # Should be two clusters, one index each
        assert len(result) == 2
        all_indices = set()
        for cluster in result:
            assert len(cluster) == 1
            all_indices.update(cluster)
        assert all_indices == {0, 1}

    def test_chain_clustering(self):
        """Points forming a chain cluster together via transitive connections.

        A---B---C where A-B close, B-C close, but A-C far
        All should be in same cluster due to B bridging them.
        """
        centers = [(0.0, 0.0), (30.0, 0.0), (60.0, 0.0)]
        # Distance 30 between neighbors, 60 between ends
        result = cluster_by_proximity(centers, distance_threshold=35.0)
        # All should be connected via chain
        assert len(result) == 1
        assert set(result[0]) == {0, 1, 2}

    def test_two_separate_clusters(self):
        """Two groups of close points form two clusters."""
        centers = [
            # Cluster 1: top-left corner
            (0.0, 0.0),
            (10.0, 10.0),
            # Cluster 2: bottom-right corner
            (200.0, 200.0),
            (210.0, 210.0),
        ]
        result = cluster_by_proximity(centers, distance_threshold=30.0)
        assert len(result) == 2

        # Extract cluster contents
        cluster_sets = [set(c) for c in result]
        assert {0, 1} in cluster_sets
        assert {2, 3} in cluster_sets

    def test_distance_threshold_boundary(self):
        """Points exactly at threshold distance are NOT connected (< not <=)."""
        # Distance = sqrt(30^2 + 40^2) = 50.0 exactly
        centers = [(0.0, 0.0), (30.0, 40.0)]
        result = cluster_by_proximity(centers, distance_threshold=50.0)
        # Should NOT connect because distance < threshold uses strict less-than
        assert len(result) == 2

    def test_custom_threshold(self):
        """Custom distance threshold works correctly."""
        centers = [(0.0, 0.0), (100.0, 0.0)]
        # With threshold 50, they're separate
        result_50 = cluster_by_proximity(centers, distance_threshold=50.0)
        assert len(result_50) == 2

        # With threshold 150, they're together
        result_150 = cluster_by_proximity(centers, distance_threshold=150.0)
        assert len(result_150) == 1

    def test_returns_indices_not_coordinates(self):
        """Function returns indices into the original list, not coordinates."""
        centers = [(100.0, 200.0), (105.0, 205.0)]
        result = cluster_by_proximity(centers, distance_threshold=20.0)
        # Should return list of lists of integers (indices)
        assert len(result) == 1
        assert all(isinstance(idx, int) for idx in result[0])
        assert set(result[0]) == {0, 1}


class TestFindNearestParagraphByY:
    """Tests for find_nearest_paragraph_by_y function."""

    def test_empty_blocks_returns_none(self):
        """Empty markdown blocks returns None."""
        result = find_nearest_paragraph_by_y(100.0, [])
        assert result is None

    def test_no_page_y_start_returns_none(self):
        """Blocks without page_y_start attribute returns None."""

        class MockBlock:
            pass

        blocks = [MockBlock()]
        result = find_nearest_paragraph_by_y(100.0, blocks)
        assert result is None

    def test_page_y_start_none_returns_none(self):
        """Blocks with page_y_start=None returns None."""

        class MockBlock:
            page_y_start = None

        blocks = [MockBlock()]
        result = find_nearest_paragraph_by_y(100.0, blocks)
        assert result is None

    def test_finds_nearest_block(self):
        """Returns index of block with closest Y coordinate."""

        class MockBlock:
            def __init__(self, y):
                self.page_y_start = y

        blocks = [MockBlock(50.0), MockBlock(150.0), MockBlock(250.0)]

        # Annotation at Y=100 should match block at Y=150 (distance=50 vs 50)
        # Actually both 50 and 150 are equidistant (50 units), but 150 wins
        # due to iteration order checking

        # Test clear winner: annotation at Y=160 should match Y=150
        result = find_nearest_paragraph_by_y(160.0, blocks)
        assert result == 1  # Block at Y=150

        # Annotation at Y=40 should match block at Y=50
        result = find_nearest_paragraph_by_y(40.0, blocks)
        assert result == 0

        # Annotation at Y=300 should match block at Y=250
        result = find_nearest_paragraph_by_y(300.0, blocks)
        assert result == 2

    def test_first_block_must_have_y(self):
        """Returns None if first block lacks page_y_start (validation shortcut)."""

        class MockBlock:
            def __init__(self, y):
                self.page_y_start = y

        class MockBlockNoY:
            pass

        # First block without page_y_start means data isn't available
        blocks = [MockBlockNoY(), MockBlock(150.0), MockBlockNoY()]
        result = find_nearest_paragraph_by_y(160.0, blocks)
        assert result is None

    def test_skips_middle_blocks_without_y(self):
        """Skips middle blocks without page_y_start and still finds valid match."""

        class MockBlock:
            def __init__(self, y):
                self.page_y_start = y

        class MockBlockNoY:
            page_y_start = None

        # First block valid, middle block invalid, last block valid
        blocks = [MockBlock(50.0), MockBlockNoY(), MockBlock(250.0)]
        # Annotation at Y=200 should match Y=250 (distance=50)
        result = find_nearest_paragraph_by_y(200.0, blocks)
        assert result == 2  # Block at Y=250


class TestClusterBboxesKdtree:
    """Tests for cluster_bboxes_kdtree function (efficient KDTree clustering)."""

    def test_empty_bboxes(self):
        """Empty input returns empty result."""
        result = cluster_bboxes_kdtree([])
        assert result == []

    def test_single_bbox(self):
        """Single bbox returns single cluster."""
        result = cluster_bboxes_kdtree([(0, 0, 10, 10)])
        assert result == [[0]]

    def test_two_close_bboxes(self):
        """Two close bboxes cluster together."""
        bboxes = [(0, 0, 10, 10), (20, 0, 10, 10)]
        result = cluster_bboxes_kdtree(bboxes, distance_threshold=30.0)
        assert len(result) == 1
        assert set(result[0]) == {0, 1}

    def test_two_distant_bboxes(self):
        """Two distant bboxes form separate clusters."""
        bboxes = [(0, 0, 10, 10), (200, 200, 10, 10)]
        result = cluster_bboxes_kdtree(bboxes, distance_threshold=50.0)
        assert len(result) == 2

    def test_chain_clustering(self):
        """Bboxes connected via chain form one cluster (transitive)."""
        # A -- B -- C (each 30px apart, threshold 40px)
        bboxes = [(0, 0, 10, 10), (30, 0, 10, 10), (60, 0, 10, 10)]
        result = cluster_bboxes_kdtree(bboxes, distance_threshold=40.0)
        # All connected via chain
        assert len(result) == 1
        assert set(result[0]) == {0, 1, 2}

    def test_multiline_handwriting(self):
        """Multi-line handwritten note clusters together.

        Simulates: "New paragraph"   (line 1)
                   "With second line" (line 2, ~35px below)
        """
        bboxes = [
            (0, 0, 100, 15),  # Line 1: "New paragraph"
            (0, 35, 120, 15),  # Line 2: "With second line"
        ]
        # Default threshold is 80px, should cluster
        result = cluster_bboxes_kdtree(bboxes, distance_threshold=80.0)
        assert len(result) == 1
        assert set(result[0]) == {0, 1}

    def test_multiline_with_multiple_words(self):
        """Multi-line note with multiple words per line clusters together."""
        bboxes = [
            # Line 1
            (0, 0, 50, 15),  # Word 1
            (60, 0, 50, 15),  # Word 2
            # Line 2 (35px below)
            (0, 35, 60, 15),  # Word 3
            (70, 35, 50, 15),  # Word 4
        ]
        result = cluster_bboxes_kdtree(bboxes, distance_threshold=80.0)
        # All should be one cluster - connected via proximity
        assert len(result) == 1
        assert set(result[0]) == {0, 1, 2, 3}

    def test_separate_annotations(self):
        """Annotations far apart form separate clusters."""
        bboxes = [
            # Margin note on left (cluster 1)
            (0, 100, 30, 50),
            # End note on right (cluster 2)
            (700, 100, 30, 50),
        ]
        result = cluster_bboxes_kdtree(bboxes, distance_threshold=80.0)
        assert len(result) == 2

    def test_bbox_size_expansion(self):
        """Large bboxes expand search radius appropriately."""
        # Two bboxes with 100px centers but 50px dimensions
        # Centers are 100px apart, but with bbox expansion they should cluster
        bboxes = [
            (0, 0, 50, 50),  # Center at (25, 25)
            (100, 0, 50, 50),  # Center at (125, 25)
        ]
        # Distance between centers: 100px
        # Threshold 80px + max_dim/2 (25px) = 105px search radius
        result = cluster_bboxes_kdtree(bboxes, distance_threshold=80.0)
        assert len(result) == 1


class TestStrokeData:
    """Tests for StrokeData dataclass."""

    def test_basic_creation(self):
        """StrokeData can be created with just bounding_box."""
        stroke = StrokeData(bounding_box=(10, 20, 30, 40))
        assert stroke.bbox == (10, 20, 30, 40)
        assert stroke.bounding_box == (10, 20, 30, 40)
        assert stroke.points == []
        assert stroke.timestamps is None
        assert stroke.color == 0  # default

    def test_full_creation(self):
        """StrokeData can be created with all fields."""
        stroke = StrokeData(
            bounding_box=(10, 20, 30, 40),
            points=[(10.0, 20.0, 0.5), (15.0, 25.0, 0.7), (20.0, 30.0, 0.3)],
            timestamps=[100.0, 110.0, 120.0],
            color=2,
            tool=1,
            thickness=3.0,
        )
        assert len(stroke.points) == 3
        assert stroke.points[0] == (10.0, 20.0, 0.5)  # (x, y, pressure)
        assert stroke.color == 2
        assert stroke.tool == 1
        assert stroke.thickness == 3.0

    def test_center_property(self):
        """Center property calculates bbox center correctly."""
        stroke = StrokeData(bounding_box=(10, 20, 30, 40))
        # Center: (10 + 30/2, 20 + 40/2) = (25, 40)
        assert stroke.center == (25.0, 40.0)


class TestKDTreeProximityStrategy:
    """Tests for KDTreeProximityStrategy class."""

    def test_name_property(self):
        """Strategy has correct name."""
        strategy = KDTreeProximityStrategy()
        assert strategy.name == "kdtree_proximity"

    def test_default_threshold(self):
        """Default threshold is 80px."""
        strategy = KDTreeProximityStrategy()
        assert strategy.distance_threshold == 80.0

    def test_custom_threshold(self):
        """Custom threshold can be set."""
        strategy = KDTreeProximityStrategy(distance_threshold=100.0)
        assert strategy.distance_threshold == 100.0

    def test_cluster_empty(self):
        """Empty strokes returns empty clusters."""
        strategy = KDTreeProximityStrategy()
        result = strategy.cluster([])
        assert result == []

    def test_cluster_single(self):
        """Single stroke returns single cluster."""
        strategy = KDTreeProximityStrategy()
        strokes = [StrokeData(bounding_box=(0, 0, 10, 10))]
        result = strategy.cluster(strokes)
        assert result == [[0]]

    def test_cluster_uses_kdtree(self):
        """Strategy uses KDTree clustering internally."""
        strategy = KDTreeProximityStrategy(distance_threshold=40.0)
        strokes = [
            StrokeData(bounding_box=(0, 0, 10, 10)),
            StrokeData(bounding_box=(30, 0, 10, 10)),
            StrokeData(bounding_box=(60, 0, 10, 10)),
        ]
        result = strategy.cluster(strokes)
        # Chain clustering should work
        assert len(result) == 1
        assert set(result[0]) == {0, 1, 2}

    def test_conforms_to_protocol(self):
        """KDTreeProximityStrategy conforms to ClusteringStrategy protocol."""
        strategy = KDTreeProximityStrategy()
        assert isinstance(strategy, ClusteringStrategy)


class TestVisualModelStrategy:
    """Tests for VisualModelStrategy class."""

    def test_name_property(self):
        """Strategy has correct name."""
        strategy = VisualModelStrategy()
        assert strategy.name == "visual_model"

    def test_raises_not_implemented(self):
        """Cluster method raises NotImplementedError."""
        strategy = VisualModelStrategy()
        strokes = [StrokeData(bounding_box=(0, 0, 10, 10))]
        with pytest.raises(NotImplementedError):
            strategy.cluster(strokes)

    def test_conforms_to_protocol(self):
        """VisualModelStrategy conforms to ClusteringStrategy protocol."""
        strategy = VisualModelStrategy()
        assert isinstance(strategy, ClusteringStrategy)


class TestGetClusteringStrategy:
    """Tests for get_clustering_strategy factory function."""

    def test_default_strategy(self):
        """Default returns KDTreeProximityStrategy."""
        strategy = get_clustering_strategy()
        assert isinstance(strategy, KDTreeProximityStrategy)

    def test_kdtree_by_name(self):
        """Can get KDTreeProximityStrategy by name."""
        strategy = get_clustering_strategy("kdtree_proximity")
        assert isinstance(strategy, KDTreeProximityStrategy)

    def test_visual_model_by_name(self):
        """Can get VisualModelStrategy by name."""
        strategy = get_clustering_strategy("visual_model")
        assert isinstance(strategy, VisualModelStrategy)

    def test_custom_threshold(self):
        """Can pass custom threshold."""
        strategy = get_clustering_strategy("kdtree_proximity", distance_threshold=120.0)
        assert strategy.distance_threshold == 120.0

    def test_unknown_strategy_raises(self):
        """Unknown strategy name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown clustering strategy"):
            get_clustering_strategy("unknown")
