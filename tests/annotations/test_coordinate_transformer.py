"""Unit tests for coordinate transformation utilities.

Tests coordinate space transformations between:
- Native (text-relative) coordinates used in .rm files
- Absolute page coordinates

Key test cases:
- Root layer (already absolute) vs text-relative layers
- Positive vs negative Y coordinates
- Edge cases with None values
"""

import pytest
from rmscene.tagged_block_common import CrdtId

from rock_paper_sync.annotations import Point, Rectangle
from rock_paper_sync.coordinate_transformer import (
    DEFAULT_TEXT_ORIGIN_X,
    DEFAULT_TEXT_ORIGIN_Y,
    NEGATIVE_Y_OFFSET,
    ROOT_LAYER_ID,
    AnchorOrigin,
    CoordinateTransformer,
    TextOrigin,
    is_root_layer,
    is_text_relative,
)


class TestIsRootLayer:
    """Tests for is_root_layer()."""

    def test_root_layer_id(self):
        """Root layer ID is correctly identified."""
        root_id = CrdtId(ROOT_LAYER_ID[0], ROOT_LAYER_ID[1])
        assert is_root_layer(root_id) is True

    def test_text_layer_id(self):
        """Text layer IDs are not root layer."""
        text_id = CrdtId(2, 530)
        assert is_root_layer(text_id) is False

    def test_different_layer_id(self):
        """Other layer IDs are not root layer."""
        other_id = CrdtId(1, 100)
        assert is_root_layer(other_id) is False


class TestIsTextRelative:
    """Tests for is_text_relative()."""

    def test_none_is_not_text_relative(self):
        """None parent_id is not text-relative."""
        assert is_text_relative(None) is False

    def test_root_layer_is_not_text_relative(self):
        """Root layer uses absolute coordinates."""
        root_id = CrdtId(ROOT_LAYER_ID[0], ROOT_LAYER_ID[1])
        assert is_text_relative(root_id) is False

    def test_text_layer_is_text_relative(self):
        """Text layers use text-relative coordinates."""
        text_id = CrdtId(2, 530)
        assert is_text_relative(text_id) is True


class TestCoordinateTransformerInit:
    """Tests for CoordinateTransformer initialization."""

    def test_default_init(self):
        """Default initialization uses device defaults."""
        transformer = CoordinateTransformer()
        assert transformer.text_origin_x == DEFAULT_TEXT_ORIGIN_X
        assert transformer.text_origin_y == DEFAULT_TEXT_ORIGIN_Y

    def test_custom_origin(self):
        """Custom text origin can be specified."""
        transformer = CoordinateTransformer(text_origin_x=-400, text_origin_y=100)
        assert transformer.text_origin_x == -400
        assert transformer.text_origin_y == 100


class TestToAbsolute:
    """Tests for to_absolute() coordinate transformation."""

    @pytest.fixture
    def transformer(self):
        """Standard transformer with known origin."""
        return CoordinateTransformer(text_origin_x=-375, text_origin_y=94)

    def test_root_layer_passthrough(self, transformer):
        """Root layer coordinates pass through unchanged."""
        root_id = CrdtId(ROOT_LAYER_ID[0], ROOT_LAYER_ID[1])
        abs_x, abs_y = transformer.to_absolute(100, 200, root_id)
        assert abs_x == 100
        assert abs_y == 200

    def test_none_parent_passthrough(self, transformer):
        """None parent_id passes through unchanged."""
        abs_x, abs_y = transformer.to_absolute(100, 200, None)
        assert abs_x == 100
        assert abs_y == 200

    def test_text_relative_positive_y(self, transformer):
        """Text-relative with positive Y uses text origin."""
        text_id = CrdtId(2, 530)
        # native_x=10, anchor_x=100 -> abs_x = 10 + 100 = 110
        # native_y=50 (positive), text_origin_y=94 -> abs_y = 94 + 0 + 50 = 144
        abs_x, abs_y = transformer.to_absolute(
            native_x=10,
            native_y=50,
            parent_id=text_id,
            anchor_x=100,
            stroke_center_y=50,  # Positive Y
        )
        assert abs_x == 110
        assert abs_y == 144

    def test_text_relative_negative_y(self, transformer):
        """Text-relative with negative Y uses negative offset."""
        text_id = CrdtId(2, 530)
        # native_x=10, anchor_x=100 -> abs_x = 10 + 100 = 110
        # native_y=-50 (negative), text_origin_y=94, offset applied
        abs_x, abs_y = transformer.to_absolute(
            native_x=10,
            native_y=-50,
            parent_id=text_id,
            anchor_x=100,
            stroke_center_y=-50,  # Negative Y triggers offset
        )
        assert abs_x == 110
        assert abs_y == 94 + NEGATIVE_Y_OFFSET + (-50)

    def test_no_anchor_x_uses_text_origin(self, transformer):
        """Without anchor_x, uses text_origin_x."""
        text_id = CrdtId(2, 530)
        abs_x, abs_y = transformer.to_absolute(
            native_x=10,
            native_y=50,
            parent_id=text_id,
            anchor_x=None,  # Not provided
            stroke_center_y=50,
        )
        # abs_x = 10 + text_origin_x = 10 + (-375) = -365
        assert abs_x == 10 + transformer.text_origin_x

    def test_no_stroke_center_assumes_positive(self, transformer):
        """Without stroke_center_y, assumes positive (no offset)."""
        text_id = CrdtId(2, 530)
        abs_x, abs_y = transformer.to_absolute(
            native_x=10,
            native_y=-50,
            parent_id=text_id,
            anchor_x=100,
            stroke_center_y=None,  # Not provided
        )
        # No negative offset applied (assumes positive space)
        assert abs_y == 94 + 0 + (-50)


class TestTransformPoint:
    """Tests for transform_point()."""

    @pytest.fixture
    def transformer(self):
        return CoordinateTransformer(text_origin_x=-375, text_origin_y=94)

    def test_transform_point_root_layer(self, transformer):
        """Point transformation for root layer."""
        root_id = CrdtId(ROOT_LAYER_ID[0], ROOT_LAYER_ID[1])
        point = Point(x=100, y=200)

        result = transformer.transform_point(point, root_id)

        assert result.x == 100
        assert result.y == 200

    def test_transform_point_text_relative(self, transformer):
        """Point transformation for text-relative layer."""
        text_id = CrdtId(2, 530)
        point = Point(x=10, y=50)

        result = transformer.transform_point(point, text_id, anchor_x=100, stroke_center_y=50)

        assert result.x == 110
        assert result.y == 144


class TestTransformBbox:
    """Tests for transform_bbox()."""

    @pytest.fixture
    def transformer(self):
        return CoordinateTransformer(text_origin_x=-375, text_origin_y=94)

    def test_transform_bbox_root_layer(self, transformer):
        """Bounding box transformation for root layer."""
        root_id = CrdtId(ROOT_LAYER_ID[0], ROOT_LAYER_ID[1])
        bbox = Rectangle(x=100, y=200, w=50, h=30)

        result = transformer.transform_bbox(bbox, root_id)

        assert result.x == 100
        assert result.y == 200
        assert result.w == 50
        assert result.h == 30

    def test_transform_bbox_preserves_dimensions(self, transformer):
        """Bbox transformation preserves width and height."""
        text_id = CrdtId(2, 530)
        bbox = Rectangle(x=10, y=50, w=100, h=50)

        result = transformer.transform_bbox(bbox, text_id, anchor_x=100)

        assert result.w == 100
        assert result.h == 50

    def test_transform_bbox_calculates_stroke_center(self, transformer):
        """Bbox transformation uses center Y for offset determination."""
        text_id = CrdtId(2, 530)
        # bbox with negative center: y=-100, h=40 -> center = -100 + 20 = -80
        bbox = Rectangle(x=10, y=-100, w=50, h=40)

        result = transformer.transform_bbox(bbox, text_id, anchor_x=100)

        # Should apply negative Y offset since center is negative
        expected_y = 94 + NEGATIVE_Y_OFFSET + (-100)
        assert result.y == expected_y


class TestDataClasses:
    """Tests for coordinate data classes."""

    def test_text_origin_defaults(self):
        """TextOrigin has sensible defaults."""
        origin = TextOrigin(x=-375, y=94)
        assert origin.x == -375
        assert origin.y == 94
        # Default width from device geometry
        assert origin.width > 0

    def test_anchor_origin(self):
        """AnchorOrigin stores x, y coordinates."""
        anchor = AnchorOrigin(x=100, y=200)
        assert anchor.x == 100
        assert anchor.y == 200


class TestEdgeCases:
    """Edge case tests for coordinate transformations."""

    def test_zero_coordinates(self):
        """Zero coordinates transform correctly."""
        transformer = CoordinateTransformer(text_origin_x=0, text_origin_y=0)
        text_id = CrdtId(2, 530)

        abs_x, abs_y = transformer.to_absolute(0, 0, text_id, anchor_x=0)

        assert abs_x == 0
        assert abs_y == 0

    def test_large_coordinates(self):
        """Large coordinates don't overflow."""
        transformer = CoordinateTransformer()
        text_id = CrdtId(2, 530)

        abs_x, abs_y = transformer.to_absolute(
            10000, 10000, text_id, anchor_x=1000, stroke_center_y=10000
        )

        assert abs_x == 11000
        assert isinstance(abs_y, float)

    def test_negative_anchor_x(self):
        """Negative anchor_x values work correctly."""
        transformer = CoordinateTransformer(text_origin_y=100)
        text_id = CrdtId(2, 530)

        abs_x, abs_y = transformer.to_absolute(
            native_x=50,
            native_y=0,
            parent_id=text_id,
            anchor_x=-200,
        )

        assert abs_x == 50 + (-200)
