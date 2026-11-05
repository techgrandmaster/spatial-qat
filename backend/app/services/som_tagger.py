"""
SoM (Set-of-Mark) tagger service.
Renders [1..N] SoM badges via Pillow on board images so muse-spark-1.3
can link masks to datasheet pages.
"""

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


def render_som_badges(
    image_path: Path,
    components: list[dict],
    output_path: Path,
) -> Path:
    """
    Render [1..N] SoM badges at component centroids on a board image.
    Each component dict should have: som_index, bbox (with x, y, w, h).
    """
    img = Image.open(str(image_path)).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Badge size scales with image
    img_w, img_h = img.size
    badge_size = max(20, int(min(img_w, img_h) * 0.03))
    font_size = int(badge_size * 0.7)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    for comp in components:
        som_index = comp.get("som_index")
        if som_index is None:
            continue

        bbox = comp.get("bbox", {})
        cx = bbox.get("x", 0.5) * img_w
        cy = bbox.get("y", 0.5) * img_h

        # Draw colored circle badge
        radius = badge_size // 2
        x0 = cx - radius
        y0 = cy - radius
        x1 = cx + radius
        y1 = cy + radius

        # Color based on index
        color = _som_color(som_index)
        draw.ellipse([x0, y0, x1, y1], fill=color, outline="white", width=2)

        # Draw number
        text = str(som_index)
        bbox_text = draw.textbbox((0, 0), text, font=font)
        tw = bbox_text[2] - bbox_text[0]
        th = bbox_text[3] - bbox_text[1]
        tx = cx - tw / 2
        ty = cy - th / 2
        draw.text((tx, ty), text, fill="white", font=font)

    img.save(str(output_path))
    return output_path


def _som_color(index: int) -> tuple[int, int, int, int]:
    """Generate a distinct color for each SoM index."""
    colors = [
        (255, 0, 0, 128),      # Red
        (0, 128, 0, 128),      # Green
        (0, 0, 255, 128),      # Blue
        (255, 165, 0, 128),    # Orange
        (128, 0, 128, 128),    # Purple
        (0, 128, 128, 128),    # Teal
        (255, 0, 255, 128),    # Magenta
        (255, 128, 0, 128),    # Dark orange
        (0, 0, 128, 128),      # Navy
        (128, 128, 0, 128),    # Olive
    ]
    return colors[(index - 1) % len(colors)]


def compute_centroid(polygons: list) -> tuple[float, float]:
    """Compute centroid of normalized polygons."""
    if not polygons:
        return 0.5, 0.5

    xs, ys = [], []
    for poly_with_holes in polygons:
        for ring in poly_with_holes:
            for point in ring:
                xs.append(point[0])
                ys.append(point[1])

    if not xs:
        return 0.5, 0.5

    return sum(xs) / len(xs), sum(ys) / len(ys)
