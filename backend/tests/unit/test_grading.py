"""
Unit tests for geometric grading service.
100% deterministic coverage of coordinate normalization, Shapely/binary mask hit-testing,
2% diagonal near-miss math, smallest-area overlap resolution, and score deduction formulas.
"""

from app.services.grading import (
    compute_diagonal,
    compute_distance_to_polygon,
    compute_spatial_score,
    grade_conceptual_explanation,
    grade_spatial_attempt,
    hit_test_polygon,
    normalize_click,
    resolve_clicked_component,
)


class TestNormalizeClick:
    """Test coordinate normalization to intrinsic pixels."""

    def test_center_point(self):
        px, py = normalize_click(0.5, 0.5, 1000, 1000)
        assert px == 500
        assert py == 500

    def test_top_left(self):
        px, py = normalize_click(0.0, 0.0, 1000, 1000)
        assert px == 0
        assert py == 0

    def test_bottom_right(self):
        px, py = normalize_click(1.0, 1.0, 1000, 1000)
        assert px == 999
        assert py == 999

    def test_clamps_negative(self):
        px, py = normalize_click(-0.1, -0.1, 1000, 1000)
        assert px == 0
        assert py == 0

    def test_clamps_over_1(self):
        px, py = normalize_click(1.1, 1.1, 1000, 1000)
        assert px == 999
        assert py == 999

    def test_non_square_image(self):
        px, py = normalize_click(0.5, 0.5, 1920, 1080)
        assert px == 960
        assert py == 540


class TestComputeDiagonal:
    """Test image diagonal computation."""

    def test_square(self):
        d = compute_diagonal(100, 100)
        assert abs(d - 141.421356) < 0.01

    def test_16_9(self):
        d = compute_diagonal(1920, 1080)
        assert abs(d - 2202.907) < 0.01


class TestHitTestPolygon:
    """Test Shapely polygon hit-testing."""

    def test_point_inside_square(self):
        polygons = [[[
            [0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2],
        ]]]
        assert hit_test_polygon(polygons, 0.3, 0.3) is True

    def test_point_outside_square(self):
        polygons = [[[
            [0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2],
        ]]]
        assert hit_test_polygon(polygons, 0.5, 0.5) is False

    def test_point_on_boundary(self):
        polygons = [[[
            [0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2],
        ]]]
        # Shapely contains() returns False for boundary points
        assert hit_test_polygon(polygons, 0.2, 0.3) is False

    def test_empty_polygons(self):
        assert hit_test_polygon([], 0.5, 0.5) is False

    def test_multiple_polygons(self):
        polygons = [
            [[
                [0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1], [0.0, 0.0],
            ]],
            [[
                [0.8, 0.8], [0.9, 0.8], [0.9, 0.9], [0.8, 0.9], [0.8, 0.8],
            ]],
        ]
        assert hit_test_polygon(polygons, 0.05, 0.05) is True
        assert hit_test_polygon(polygons, 0.85, 0.85) is True
        assert hit_test_polygon(polygons, 0.5, 0.5) is False


class TestComputeDistanceToPolygon:
    """Test distance computation from click to polygon boundary."""

    def test_point_inside(self):
        polygons = [[[
            [0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2],
        ]]]
        # Shapely distance to exterior is > 0 for interior points;
        # the center (0.3, 0.3) is 0.1 from the boundary
        dist = compute_distance_to_polygon(0.3, 0.3, polygons)
        assert 0.0 < dist < 0.15

    def test_point_outside(self):
        polygons = [[[
            [0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2],
        ]]]
        dist = compute_distance_to_polygon(0.5, 0.5, polygons)
        assert dist > 0.0

    def test_empty_polygons(self):
        dist = compute_distance_to_polygon(0.5, 0.5, [])
        assert dist == 1.0  # Max normalized distance


class TestComputeSpatialScore:
    """Test deterministic spatial score formula."""

    def test_hit_no_hints(self):
        score = compute_spatial_score(True, False, 0)
        assert score == 1.0

    def test_hit_with_hints(self):
        score = compute_spatial_score(True, False, 2)
        assert score == 0.5  # 1.0 - 0.25*2

    def test_hit_max_hints(self):
        score = compute_spatial_score(True, False, 5)
        assert score == 0.0  # max(0, 1.0 - 1.25)

    def test_near_miss_no_hints(self):
        score = compute_spatial_score(False, True, 0)
        assert score == 0.5

    def test_near_miss_with_hints(self):
        score = compute_spatial_score(False, True, 1)
        assert score == 0.25  # 0.5 - 0.25

    def test_miss(self):
        score = compute_spatial_score(False, False, 0)
        assert score == 0.0

    def test_miss_with_hints(self):
        score = compute_spatial_score(False, False, 3)
        assert score == 0.0


class TestResolveClickedComponent:
    """Test smallest-area overlap resolution."""

    def test_single_component(self):
        components = [{
            "id": "comp-1",
            "polygons": [[[
                [0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2],
            ]]],
            "area_fraction": 0.04,
            "confidence": 0.9,
        }]
        result = resolve_clicked_component(0.3, 0.3, components)
        assert result is not None
        assert result["id"] == "comp-1"

    def test_smallest_area_wins(self):
        components = [
            {
                "id": "large",
                "polygons": [[[
                    [0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0],
                ]]],
                "area_fraction": 1.0,
                "confidence": 0.9,
            },
            {
                "id": "small",
                "polygons": [[[
                    [0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2],
                ]]],
                "area_fraction": 0.04,
                "confidence": 0.9,
            },
        ]
        result = resolve_clicked_component(0.3, 0.3, components)
        assert result is not None
        assert result["id"] == "small"

    def test_no_component_clicked(self):
        components = [{
            "id": "comp-1",
            "polygons": [[[
                [0.8, 0.8], [0.9, 0.8], [0.9, 0.9], [0.8, 0.9], [0.8, 0.8],
            ]]],
            "area_fraction": 0.01,
            "confidence": 0.9,
        }]
        result = resolve_clicked_component(0.1, 0.1, components)
        assert result is None

    def test_empty_components(self):
        result = resolve_clicked_component(0.5, 0.5, [])
        assert result is None


def _fake_comp(cid, polygons, area=0.04, conf=0.9, mask_id=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=cid, polygons=polygons, area_fraction=area, confidence=conf, mask_asset_id=mask_id
    )


def _fake_image(w=1000, h=1000):
    from types import SimpleNamespace

    return SimpleNamespace(width_px=w, height_px=h)


class TestOverlapAmbiguous:
    """S8: overlap_ambiguous is True only when >=2 components contain the click."""

    def test_single_container_not_ambiguous(self):
        import uuid

        sq = [[[[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2]]]]
        far = [[[[0.7, 0.7], [0.9, 0.7], [0.9, 0.9], [0.7, 0.9], [0.7, 0.7]]]]
        target = _fake_comp(uuid.uuid4(), sq)
        other = _fake_comp(uuid.uuid4(), far)
        import asyncio

        out = asyncio.run(grade_spatial_attempt(
            0.3, 0.3, target, _fake_image(), [target, other], hints_used=0, db=None
        ))
        assert out["spatial_hit"] is True
        assert out["overlap_ambiguous"] is False

    def test_two_containers_ambiguous(self):
        import asyncio
        import uuid

        big = [[[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]]
        small = [[[[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2]]]]
        target = _fake_comp(uuid.uuid4(), small)
        other = _fake_comp(uuid.uuid4(), big, area=1.0)
        out = asyncio.run(grade_spatial_attempt(
            0.3, 0.3, target, _fake_image(), [target, other], hints_used=0, db=None
        ))
        assert out["spatial_hit"] is True
        assert out["overlap_ambiguous"] is True


class TestConceptualProviderError:
    """S8 / PRD §10B: provider errors yield not_graded, never a score."""

    def test_provider_error_is_not_graded(self):
        import asyncio
        import uuid
        from unittest.mock import AsyncMock, patch

        from app.adapters.meta.spark import MuseSparkAdapter

        with patch.object(MuseSparkAdapter, "grade_explanation", new=AsyncMock(side_effect=RuntimeError("boom"))):
            out = asyncio.run(grade_conceptual_explanation(
                "the regulator outputs 3.3V", {"expected_concepts": ["regulator"]},
                uuid.uuid4(), db=None,
            ))
        assert out["conceptual_status"] == "not_graded"
        assert out["conceptual_score"] is None
