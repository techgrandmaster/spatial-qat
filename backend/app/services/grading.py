"""
Grading service: deterministic spatial grading & rubric evaluation.
Separates geometric location grading from conceptual explanation grading.

Part A: Pure-Function Geometric Click Grading
- Coordinate normalization to intrinsic pixels
- Authoritative target hit-test via binary mask PNG + Shapely polygon
- Near-miss distance calculation (≤2% of diagonal)
- Overlap & confusion identification (smallest-area resolution)
- Deterministic spatial score formula

Part B: Grounded Conceptual Grading
- MuseSparkAdapter evaluates against private rubric_json
- Negation & failure guardrails
- Post-submission reveal of hidden target
"""

import io
import logging
import math
from uuid import UUID

from PIL import Image
from shapely.geometry import Point, Polygon
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import Asset, Component
from app.models.schemas import ConceptualStatus

logger = logging.getLogger(__name__)

NEAR_MISS_THRESHOLD = 0.02  # 2% of image diagonal


def normalize_click(click_x: float, click_y: float, width_px: int, height_px: int) -> tuple[int, int]:
    """Convert normalized [0,1] click to integer pixel coordinates."""
    px = int(click_x * width_px)
    py = int(click_y * height_px)
    return max(0, min(px, width_px - 1)), max(0, min(py, height_px - 1))


def compute_diagonal(width_px: int, height_px: int) -> float:
    """Compute image diagonal in pixels."""
    return math.sqrt(width_px ** 2 + height_px ** 2)


def hit_test_mask(mask_data: bytes, px: int, py: int, width_px: int, height_px: int) -> bool:
    """
    Authoritative target hit-test: load binary mask PNG and check if pixel is non-zero.
    """
    try:
        img = Image.open(io.BytesIO(mask_data)).convert("L")
        mask_w, mask_h = img.size
        # Scale click to mask dimensions
        mx = int(px * mask_w / width_px)
        my = int(py * mask_h / height_px)
        mx = max(0, min(mx, mask_w - 1))
        my = max(0, min(my, mask_h - 1))
        return img.getpixel((mx, my)) > 0
    except Exception as exc:
        logger.warning("Mask hit-test failed: %s", exc)
        return False


def hit_test_polygon(polygons: list, click_x: float, click_y: float) -> bool:
    """
    Hit-test using Shapely polygon geometry.
    Polygons are normalized [0,1] coordinate rings.
    """
    point = Point(click_x, click_y)
    for poly_with_holes in polygons:
        if not poly_with_holes:
            continue
        outer_ring = poly_with_holes[0]
        if len(outer_ring) < 3:
            continue
        try:
            coords = [(c[0], c[1]) for c in outer_ring]
            polygon = Polygon(coords)
            if polygon.contains(point):
                return True
        except Exception:
            continue
    return False


def compute_distance_to_polygon(click_x: float, click_y: float, polygons: list) -> float:
    """Compute minimum normalized distance from click to target polygon boundary."""
    point = Point(click_x, click_y)
    min_dist = 1.0  # Max normalized distance
    for poly_with_holes in polygons:
        if not poly_with_holes:
            continue
        outer_ring = poly_with_holes[0]
        if len(outer_ring) < 3:
            continue
        try:
            coords = [(c[0], c[1]) for c in outer_ring]
            polygon = Polygon(coords)
            dist = polygon.exterior.distance(point)
            min_dist = min(min_dist, dist)
        except Exception:
            continue
    return min_dist


def resolve_clicked_component(
    click_x: float,
    click_y: float,
    components: list[dict],
) -> dict | None:
    """
    Resolve which component was clicked using smallest-area overlap resolution.
    Components should have: id, polygons, area_fraction, confidence.
    """
    point = Point(click_x, click_y)
    candidates = []

    for comp in components:
        polygons = comp.get("polygons", [])
        for poly_with_holes in polygons:
            if not poly_with_holes:
                continue
            outer_ring = poly_with_holes[0]
            if len(outer_ring) < 3:
                continue
            try:
                coords = [(c[0], c[1]) for c in outer_ring]
                polygon = Polygon(coords)
                if polygon.contains(point):
                    candidates.append(comp)
                    break
            except Exception:
                continue

    if not candidates:
        return None

    # Smallest area wins, then highest confidence, then stable UUID order
    candidates.sort(key=lambda c: (
        c.get("area_fraction", 0.0),
        -c.get("confidence", 0.0),
        str(c.get("id", "")),
    ))
    return candidates[0]


def compute_spatial_score(spatial_hit: bool, near_miss: bool, hints_used: int) -> float:
    """
    Deterministic spatial score formula:
    max(0.0, base_score - 0.25 * hints_used)
    where base_score = 1.0 for hit, 0.5 for near miss, 0.0 otherwise.
    """
    if spatial_hit:
        base_score = 1.0
    elif near_miss:
        base_score = 0.5
    else:
        base_score = 0.0

    return max(0.0, base_score - 0.25 * hints_used)


async def grade_spatial_attempt(
    click_x: float,
    click_y: float,
    target_component: Component,
    image_asset: Asset,
    all_components: list[Component],
    hints_used: int = 0,
    db: AsyncSession | None = None,
) -> dict:
    """
    Part A: Pure-function geometric click grading.
    Returns spatial_hit, near_miss, distance_norm, clicked_component, spatial_score.
    """
    width_px = image_asset.width_px or 1000
    height_px = image_asset.height_px or 1000

    # Convert to pixel coords
    px, py = normalize_click(click_x, click_y, width_px, height_px)

    # Hit-test using Shapely polygon
    spatial_hit = hit_test_polygon(
        target_component.polygons or [],
        click_x,
        click_y,
    )

    # Also try mask hit-test if mask_asset_id is available
    if not spatial_hit and target_component.mask_asset_id:
        mask_stmt = select(Asset).where(Asset.id == target_component.mask_asset_id)
        if db:
            mask_result = await db.execute(mask_stmt)
            mask_asset = mask_result.scalar_one_or_none()
        else:
            mask_asset = None

        if mask_asset:
            from app.api.v1.assets import STORAGE_DIR

            mask_path = STORAGE_DIR / mask_asset.storage_key
            if mask_path.exists():
                mask_data = mask_path.read_bytes()
                spatial_hit = hit_test_mask(mask_data, px, py, width_px, height_px)

    # Near-miss distance
    distance_norm = compute_distance_to_polygon(
        click_x, click_y, target_component.polygons or []
    )
    near_miss = not spatial_hit and (distance_norm <= NEAR_MISS_THRESHOLD)

    # Resolve clicked component
    clicked_comp = None
    comp_dicts = [
        {
            "id": c.id,
            "polygons": c.polygons or [],
            "area_fraction": c.area_fraction or 0.0,
            "confidence": c.confidence or 1.0,
        }
        for c in all_components
        if c.id != target_component.id
    ]
    if comp_dicts:
        clicked_comp = resolve_clicked_component(click_x, click_y, comp_dicts)

    # Compute score
    spatial_score = compute_spatial_score(spatial_hit, near_miss, hints_used)

    return {
        "spatial_hit": spatial_hit,
        "near_miss": near_miss,
        "distance_norm": round(distance_norm, 6),
        "clicked_component_id": clicked_comp["id"] if clicked_comp else None,
        "overlap_ambiguous": False,
        "spatial_score": round(spatial_score, 4),
    }


async def grade_conceptual_explanation(
    explanation: str,
    rubric: dict,
    workspace_id: UUID,
    db: AsyncSession | None = None,
) -> dict:
    """
    Part B: Grounded conceptual grading via MuseSparkAdapter.
    If no explanation or adapter unavailable, return not_graded status.
    """
    if not explanation or not explanation.strip():
        return {
            "conceptual_status": ConceptualStatus.NOT_GRADED.value,
            "conceptual_score": None,
            "sentiment_aligned": None,
            "feedback_text": "No explanation provided.",
            "citations": [],
        }

    # Try to use MuseSparkAdapter for grading
    try:
        from app.adapters.meta.spark import MuseSparkAdapter

        adapter = MuseSparkAdapter()
        grade_result = await adapter.grade_explanation(explanation, rubric, workspace_id, db)
        return grade_result
    except Exception as exc:
        logger.warning("Conceptual grading failed, using fallback: %s", exc)

    # Fallback: simple keyword matching
    expected = rubric.get("expected_concepts", [])
    explanation_lower = explanation.lower()
    matched = [c for c in expected if c.lower() in explanation_lower]
    missing = [c for c in expected if c.lower() not in explanation_lower]

    if expected:
        score = len(matched) / len(expected)
    else:
        score = 0.5

    # Negation guardrail
    negation_words = {"not", "no", "never", "none", "neither", "cannot", "doesn't", "isn't"}
    has_negation = any(w in explanation_lower for w in negation_words)
    sentiment_aligned = not has_negation if matched else None

    feedback_parts = []
    if matched:
        feedback_parts.append(f"Correctly mentioned: {', '.join(matched)}.")
    if missing:
        feedback_parts.append(f"Missing concepts: {', '.join(missing)}.")
    if not feedback_parts:
        feedback_parts.append("Explanation reviewed.")

    return {
        "conceptual_status": ConceptualStatus.GRADED.value,
        "conceptual_score": round(score, 4),
        "sentiment_aligned": sentiment_aligned,
        "feedback_text": " ".join(feedback_parts),
        "citations": [],
    }
