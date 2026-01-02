"""Tests for the coordinates module.

Tests cover:
- PageLayout: uniform, non-uniform, page boundary calculations
- DocumentPoint: creation, validation, conversion methods
- PageLocalPoint: conversion to document space
- TextRelativePoint: conversion to document space
- AnchorRelativePoint: conversion with dual-anchor Y offset
- is_root_layer: root layer detection
- Specific boundary tests from the plan
"""

import pytest
from rmscene.tagged_block_common import CrdtId

from rock_paper_sync.coordinates import (
    DEFAULT_LAYOUT,
    END_OF_DOC_MARKER,
    NEGATIVE_Y_OFFSET,
    PAGE_CENTER_X,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    AnchorPoint,
    AnchorRelativePoint,
    DocumentPoint,
    PageLayout,
    PageLocalPoint,
    PaginationPolicy,
    TextOrigin,
    TextRelativePoint,
    is_root_layer,
)


class TestPaginationPolicy:
    """Tests for PaginationPolicy enum."""

    def test_values(self):
        assert PaginationPolicy.CONTINUOUS.value == "continuous"
        assert PaginationPolicy.STRICT_PAGE.value == "strict"


class TestPageLayout:
    """Tests for PageLayout."""

    def test_uniform_default(self):
        layout = PageLayout.uniform()
        assert layout.default_height == PAGE_HEIGHT
        assert layout.page_heights == ()

    def test_uniform_custom_height(self):
        layout = PageLayout.uniform(1000.0)
        assert layout.default_height == 1000.0
        assert layout.height_of(0) == 1000.0
        assert layout.height_of(100) == 1000.0

    def test_non_uniform_heights(self):
        layout = PageLayout(page_heights=(1872.0, 1404.0, 2000.0), default_height=1872.0)
        assert layout.height_of(0) == 1872.0
        assert layout.height_of(1) == 1404.0
        assert layout.height_of(2) == 2000.0
        assert layout.height_of(3) == 1872.0  # Falls back to default

    def test_y_start_of_uniform(self):
        layout = PageLayout.uniform(1000.0)
        assert layout.y_start_of(0) == 0.0
        assert layout.y_start_of(1) == 1000.0
        assert layout.y_start_of(2) == 2000.0
        assert layout.y_start_of(5) == 5000.0

    def test_y_start_of_non_uniform(self):
        layout = PageLayout(page_heights=(100.0, 200.0, 300.0), default_height=1000.0)
        assert layout.y_start_of(0) == 0.0
        assert layout.y_start_of(1) == 100.0
        assert layout.y_start_of(2) == 300.0  # 100 + 200
        assert layout.y_start_of(3) == 600.0  # 100 + 200 + 300
        assert layout.y_start_of(4) == 1600.0  # 600 + 1000

    def test_page_for_y_uniform(self):
        layout = PageLayout.uniform(1000.0)
        assert layout.page_for_y(0.0) == 0
        assert layout.page_for_y(500.0) == 0
        assert layout.page_for_y(999.9) == 0
        assert layout.page_for_y(1000.0) == 1
        assert layout.page_for_y(1500.0) == 1
        assert layout.page_for_y(2000.0) == 2

    def test_page_for_y_negative(self):
        layout = PageLayout.uniform()
        assert layout.page_for_y(-100.0) == 0

    def test_page_for_y_non_uniform(self):
        layout = PageLayout(page_heights=(100.0, 200.0), default_height=1000.0)
        assert layout.page_for_y(50.0) == 0
        assert layout.page_for_y(100.0) == 1
        assert layout.page_for_y(299.0) == 1
        assert layout.page_for_y(300.0) == 2  # Into default pages
        assert layout.page_for_y(1300.0) == 3


class TestDocumentPoint:
    """Tests for DocumentPoint."""

    def test_create_valid(self):
        point = DocumentPoint.create(100.0, 200.0)
        assert point.x == 100.0
        assert point.y == 200.0

    def test_create_at_bounds(self):
        # X at 0
        point = DocumentPoint.create(0.0, 100.0)
        assert point.x == 0.0

        # X at PAGE_WIDTH
        point = DocumentPoint.create(PAGE_WIDTH, 100.0)
        assert point.x == PAGE_WIDTH

        # Y at 0
        point = DocumentPoint.create(100.0, 0.0)
        assert point.y == 0.0

    def test_create_invalid_x_negative(self):
        with pytest.raises(ValueError, match="outside page bounds"):
            DocumentPoint.create(-1.0, 100.0)

    def test_create_invalid_x_too_large(self):
        with pytest.raises(ValueError, match="outside page bounds"):
            DocumentPoint.create(PAGE_WIDTH + 1, 100.0)

    def test_create_invalid_y_negative(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            DocumentPoint.create(100.0, -1.0)

    def test_unsafe_no_validation(self):
        # Should not raise even with invalid values
        point = DocumentPoint.unsafe(-100.0, -200.0)
        assert point.x == -100.0
        assert point.y == -200.0

    def test_page_index_page_0(self):
        point = DocumentPoint(100.0, 500.0)
        assert point.page_index() == 0

    def test_page_index_exact_boundary(self):
        # At exactly PAGE_HEIGHT, should be on page 1
        point = DocumentPoint(100.0, PAGE_HEIGHT)
        assert point.page_index() == 1

    def test_page_index_epsilon_below(self):
        # Just below PAGE_HEIGHT, should be on page 0
        point = DocumentPoint(100.0, PAGE_HEIGHT - 0.001)
        assert point.page_index() == 0

    def test_page_index_page_2(self):
        point = DocumentPoint(100.0, PAGE_HEIGHT * 2 + 100)
        assert point.page_index() == 2

    def test_page_index_custom_layout(self):
        layout = PageLayout.uniform(1000.0)
        point = DocumentPoint(100.0, 1500.0)
        assert point.page_index(layout) == 1

    def test_to_page_local(self):
        point = DocumentPoint(100.0, PAGE_HEIGHT + 500.0)
        local = point.to_page_local()
        assert local.page == 1
        assert local.x == 100.0
        assert local.y == 500.0

    def test_to_page_local_custom_layout(self):
        layout = PageLayout(page_heights=(1000.0, 500.0), default_height=1872.0)
        point = DocumentPoint(100.0, 1200.0)  # In page 1 (starts at 1000)
        local = point.to_page_local(layout)
        assert local.page == 1
        assert local.x == 100.0
        assert local.y == 200.0  # 1200 - 1000

    def test_to_text_relative(self):
        origin = TextOrigin(x=-375.0, y=234.0)
        point = DocumentPoint(702.0, 500.0)  # At page center
        relative = point.to_text_relative(origin)
        assert relative.x == 0.0  # 702 - 702 (PAGE_CENTER_X)
        assert relative.y == 500.0 - 234.0


class TestPageLocalPoint:
    """Tests for PageLocalPoint."""

    def test_to_document_page_0(self):
        local = PageLocalPoint(page=0, x=100.0, y=500.0)
        doc = local.to_document()
        assert doc.x == 100.0
        assert doc.y == 500.0

    def test_to_document_page_1(self):
        local = PageLocalPoint(page=1, x=100.0, y=500.0)
        doc = local.to_document()
        assert doc.x == 100.0
        assert doc.y == PAGE_HEIGHT + 500.0

    def test_to_document_custom_layout(self):
        layout = PageLayout(page_heights=(1000.0, 500.0), default_height=1872.0)
        local = PageLocalPoint(page=2, x=100.0, y=200.0)
        doc = local.to_document(layout)
        assert doc.x == 100.0
        # Page 2 starts at 1000 + 500 = 1500
        assert doc.y == 1500.0 + 200.0

    def test_roundtrip_document_to_page_local(self):
        original = DocumentPoint(500.0, PAGE_HEIGHT * 2 + 300.0)
        local = original.to_page_local()
        roundtrip = local.to_document()
        assert roundtrip.x == original.x
        assert abs(roundtrip.y - original.y) < 0.001


class TestTextRelativePoint:
    """Tests for TextRelativePoint."""

    def test_to_document(self):
        origin = TextOrigin(x=-375.0, y=234.0)
        relative = TextRelativePoint(x=-375.0, y=100.0)  # Left edge of text
        doc = relative.to_document(origin)
        assert doc.x == PAGE_CENTER_X - 375.0  # 702 - 375 = 327
        assert doc.y == 234.0 + 100.0

    def test_to_document_center(self):
        origin = TextOrigin(x=-375.0, y=234.0)
        relative = TextRelativePoint(x=0.0, y=0.0)
        doc = relative.to_document(origin)
        assert doc.x == PAGE_CENTER_X  # 702
        assert doc.y == 234.0

    def test_roundtrip_document_to_text_relative(self):
        origin = TextOrigin(x=-375.0, y=234.0)
        original = DocumentPoint(500.0, 400.0)
        relative = original.to_text_relative(origin)
        roundtrip = relative.to_document(origin)
        assert abs(roundtrip.x - original.x) < 0.001
        assert abs(roundtrip.y - original.y) < 0.001


class TestAnchorRelativePoint:
    """Tests for AnchorRelativePoint and dual-anchor Y offset."""

    def test_to_document_positive_y(self):
        anchor = AnchorPoint(x=100.0, y=500.0)
        relative = AnchorRelativePoint(x=10.0, y=20.0)
        doc = relative.to_document(anchor)
        assert doc.x == 110.0
        assert doc.y == 520.0  # No offset for positive Y

    def test_to_document_negative_y_applies_offset(self):
        anchor = AnchorPoint(x=100.0, y=500.0)
        relative = AnchorRelativePoint(x=10.0, y=-5.0)
        doc = relative.to_document(anchor)
        assert doc.x == 110.0
        # With negative Y, apply NEGATIVE_Y_OFFSET (82px)
        assert doc.y == 500.0 + NEGATIVE_Y_OFFSET - 5.0

    def test_to_document_zero_y(self):
        anchor = AnchorPoint(x=100.0, y=500.0)
        relative = AnchorRelativePoint(x=10.0, y=0.0)
        doc = relative.to_document(anchor)
        assert doc.x == 110.0
        assert doc.y == 500.0  # No offset for y=0

    def test_negative_y_offset_value(self):
        # Verify the offset value is correct (82 = line_height + baseline_offset)
        assert NEGATIVE_Y_OFFSET == 82.0


class TestIsRootLayer:
    """Tests for is_root_layer function."""

    def test_root_layer_true(self):
        root_id = CrdtId(0, 11)
        assert is_root_layer(root_id) is True

    def test_root_layer_false_different_part1(self):
        not_root = CrdtId(1, 11)
        assert is_root_layer(not_root) is False

    def test_root_layer_false_different_part2(self):
        not_root = CrdtId(0, 12)
        assert is_root_layer(not_root) is False

    def test_root_layer_false_typical(self):
        typical_parent = CrdtId(2, 530)
        assert is_root_layer(typical_parent) is False

    def test_root_layer_none(self):
        # None parent means absolute coordinates (no anchor to transform from)
        assert is_root_layer(None) is True


class TestDefaultLayout:
    """Tests for DEFAULT_LAYOUT constant."""

    def test_default_layout_is_uniform(self):
        assert DEFAULT_LAYOUT.default_height == PAGE_HEIGHT
        assert DEFAULT_LAYOUT.page_heights == ()

    def test_default_layout_page_for_y(self):
        assert DEFAULT_LAYOUT.page_for_y(100.0) == 0
        assert DEFAULT_LAYOUT.page_for_y(PAGE_HEIGHT) == 1


class TestBoundaryConditions:
    """Specific boundary tests from the plan."""

    def test_exact_page_boundary(self):
        # DocumentPoint(0, 1872).page_index() == 1
        point = DocumentPoint(0, PAGE_HEIGHT)
        assert point.page_index() == 1

    def test_epsilon_below_page_boundary(self):
        # DocumentPoint(0, 1871.999).page_index() == 0
        point = DocumentPoint(0, PAGE_HEIGHT - 0.001)
        assert point.page_index() == 0

    def test_anchor_relative_negative_y_applies_offset(self):
        # AnchorRelativePoint(0, -1).to_document() applies 82px offset
        anchor = AnchorPoint(x=100.0, y=500.0)
        relative = AnchorRelativePoint(x=0.0, y=-1.0)
        doc = relative.to_document(anchor)
        expected_y = 500.0 + NEGATIVE_Y_OFFSET - 1.0
        assert doc.y == expected_y

    def test_anchor_relative_positive_y_no_offset(self):
        # AnchorRelativePoint(0, 1).to_document() no offset
        anchor = AnchorPoint(x=100.0, y=500.0)
        relative = AnchorRelativePoint(x=0.0, y=1.0)
        doc = relative.to_document(anchor)
        assert doc.y == 501.0  # No offset

    def test_is_root_layer_true_for_0_11(self):
        # is_root_layer(CrdtId(0, 11)) returns True
        assert is_root_layer(CrdtId(0, 11)) is True

    def test_is_root_layer_false_for_2_530(self):
        # is_root_layer(CrdtId(2, 530)) returns False
        assert is_root_layer(CrdtId(2, 530)) is False


class TestEndOfDocMarker:
    """Tests for END_OF_DOC_MARKER constant."""

    def test_value(self):
        # Should be 0xFFFFFFFFFFFF
        assert END_OF_DOC_MARKER == 0xFFFFFFFFFFFF
        assert END_OF_DOC_MARKER == 281474976710655


class TestTextOrigin:
    """Tests for TextOrigin."""

    def test_default_values(self):
        origin = TextOrigin()
        assert origin.x == -375.0
        assert origin.y == 234.0
        assert origin.width == 750.0

    def test_custom_values(self):
        origin = TextOrigin(x=-400.0, y=200.0, width=800.0)
        assert origin.x == -400.0
        assert origin.y == 200.0
        assert origin.width == 800.0


class TestAnchorPoint:
    """Tests for AnchorPoint."""

    def test_basic(self):
        anchor = AnchorPoint(x=100.0, y=500.0)
        assert anchor.x == 100.0
        assert anchor.y == 500.0
        assert anchor.char_offset is None

    def test_with_char_offset(self):
        anchor = AnchorPoint(x=100.0, y=500.0, char_offset=42)
        assert anchor.char_offset == 42
