"""Tests for Stroke and StrokeCluster abstractions.

Tests the CRDT-aware Stroke class and spatial StrokeCluster class.
"""

from rmscene import CrdtId

from rock_paper_sync.annotations.core.types import Point
from rock_paper_sync.annotations.domain.stroke import Stroke
from rock_paper_sync.annotations.domain.stroke_cluster import StrokeCluster


class TestStroke:
    """Tests for the Stroke class."""

    def test_stroke_center(self):
        """Test center calculation from bounding box."""
        # Create a mock stroke with known bounding box
        stroke = Stroke(
            stroke_id=CrdtId(2, 1),
            points=[Point(0, 0, 1.0, 2.0, 0.0), Point(100, 100, 1.0, 2.0, 0.0)],
            bounding_box=(0, 0, 100, 100),
            color=0,
            tool=0,
            thickness=2.0,
            tree_node_id=CrdtId(2, 1),
            line_block=None,  # type: ignore
        )
        assert stroke.center == (50.0, 50.0)
        assert stroke.center_y == 50.0

    def test_stroke_to_stroke_data(self):
        """Test conversion to lightweight StrokeData."""
        points = [Point(10, 20, 0.5, 1.5, 0.1)]
        stroke = Stroke(
            stroke_id=CrdtId(2, 1),
            points=points,
            bounding_box=(10, 20, 30, 40),
            color=1,
            tool=2,
            thickness=3.0,
            tree_node_id=CrdtId(2, 1),
            line_block=None,  # type: ignore
        )

        stroke_data = stroke.to_stroke_data()
        assert stroke_data.bounding_box == (10, 20, 30, 40)
        assert stroke_data.color == 1
        assert stroke_data.tool == 2
        assert stroke_data.thickness == 3.0
        assert len(stroke_data.points) == 1


class TestStrokeCluster:
    """Tests for the StrokeCluster class."""

    def _make_stroke(
        self, stroke_id: int, x: float, y: float, w: float = 10.0, h: float = 10.0
    ) -> Stroke:
        """Helper to create a stroke with given position."""
        return Stroke(
            stroke_id=CrdtId(2, stroke_id),
            points=[Point(x, y, 1.0, 2.0, 0.0)],
            bounding_box=(x, y, w, h),
            color=0,
            tool=0,
            thickness=2.0,
            tree_node_id=CrdtId(2, stroke_id),
            line_block=None,  # type: ignore
        )

    def test_cluster_from_single_stroke(self):
        """Test clustering a single stroke."""
        stroke = self._make_stroke(1, 0, 0)
        clusters = StrokeCluster.from_strokes([stroke])

        assert len(clusters) == 1
        assert len(clusters[0].strokes) == 1
        assert clusters[0].bounding_box == (0, 0, 10, 10)

    def test_cluster_nearby_strokes(self):
        """Test that nearby strokes cluster together."""
        # Three strokes close together (within 80px default threshold)
        strokes = [
            self._make_stroke(1, 0, 0),
            self._make_stroke(2, 30, 0),
            self._make_stroke(3, 60, 0),
        ]
        clusters = StrokeCluster.from_strokes(strokes)

        # Should all be in one cluster (chained proximity)
        assert len(clusters) == 1
        assert len(clusters[0].strokes) == 3

    def test_cluster_distant_strokes(self):
        """Test that distant strokes form separate clusters."""
        # Two strokes far apart (beyond 80px threshold)
        strokes = [
            self._make_stroke(1, 0, 0),
            self._make_stroke(2, 500, 500),
        ]
        clusters = StrokeCluster.from_strokes(strokes)

        # Should be in separate clusters
        assert len(clusters) == 2
        assert all(len(c.strokes) == 1 for c in clusters)

    def test_cluster_combined_bounding_box(self):
        """Test that cluster bounding box encompasses all strokes."""
        strokes = [
            self._make_stroke(1, 0, 0, 10, 10),
            self._make_stroke(2, 50, 50, 20, 20),
        ]
        clusters = StrokeCluster.from_strokes(strokes, distance_threshold=100)

        assert len(clusters) == 1
        bbox = clusters[0].bounding_box
        # Should encompass (0,0) to (70,70)
        assert bbox[0] == 0  # min_x
        assert bbox[1] == 0  # min_y
        assert bbox[2] == 70  # width (0 to 50+20)
        assert bbox[3] == 70  # height (0 to 50+20)

    def test_cluster_center(self):
        """Test cluster center calculation."""
        strokes = [
            self._make_stroke(1, 0, 0, 100, 100),
        ]
        clusters = StrokeCluster.from_strokes(strokes)

        assert len(clusters) == 1
        assert clusters[0].center == (50.0, 50.0)
        assert clusters[0].center_y == 50.0

    def test_cluster_id_deterministic(self):
        """Test that cluster ID is deterministic for same strokes."""
        strokes = [
            self._make_stroke(1, 0, 0),
            self._make_stroke(2, 30, 0),
        ]

        clusters1 = StrokeCluster.from_strokes(strokes)
        clusters2 = StrokeCluster.from_strokes(strokes)

        assert clusters1[0].cluster_id == clusters2[0].cluster_id

    def test_empty_strokes(self):
        """Test handling of empty stroke list."""
        clusters = StrokeCluster.from_strokes([])
        assert clusters == []

    def test_custom_distance_threshold(self):
        """Test clustering with custom distance threshold."""
        # Two strokes 50px apart
        strokes = [
            self._make_stroke(1, 0, 0),
            self._make_stroke(2, 50, 0),
        ]

        # With threshold=30, they should be separate
        clusters = StrokeCluster.from_strokes(strokes, distance_threshold=30)
        assert len(clusters) == 2

        # With threshold=100, they should cluster
        clusters = StrokeCluster.from_strokes(strokes, distance_threshold=100)
        assert len(clusters) == 1
